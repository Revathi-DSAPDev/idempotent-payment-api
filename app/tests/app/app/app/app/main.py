import datetime as dt
import hashlib
import json

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import gateway
from .database import Base, engine, get_db
from .models import IdempotencyRecord, Payment

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Idempotent Payment API")

KEY_TTL_HOURS = 24
STALE_IN_PROGRESS_SECONDS = 30


class PaymentRequest(BaseModel):
    user_id: str
    order_id: str
    amount_inr: int = Field(gt=0)


def hash_request(req: PaymentRequest) -> str:
    raw = json.dumps(req.model_dump(), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


@app.post("/pay")
def pay(
    req: PaymentRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    req_hash = hash_request(req)
    now = dt.datetime.utcnow()

    # 1. Atomically claim the key. The unique constraint lets only ONE
    #    request insert; every parallel duplicate gets IntegrityError.
    record = IdempotencyRecord(
        user_id=req.user_id,
        key=idempotency_key,
        request_hash=req_hash,
        status="IN_PROGRESS",
        expires_at=now + dt.timedelta(hours=KEY_TTL_HOURS),
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _handle_existing(db, req, idempotency_key, req_hash, now)

    # 2. We own the key. Charge (gateway also dedupes on the same key).
    try:
        result = gateway.charge(
            f"{req.user_id}:{idempotency_key}", req.amount_inr, req.order_id
        )
        # 3. One successful payment per order, across all clients.
        db.add(Payment(
            order_id=req.order_id,
            user_id=req.user_id,
            amount_inr=req.amount_inr,
            gateway_ref=result["gateway_ref"],
        ))
        record.status = "DONE"
        record.response = json.dumps(result)
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        record = _get_record(db, req.user_id, idempotency_key)
        record.status = "FAILED"
        record.response = json.dumps({"error": "Order already paid"})
        db.commit()
        raise HTTPException(409, "Order already paid")
    except Exception:
        db.rollback()
        record = _get_record(db, req.user_id, idempotency_key)
        record.status = "FAILED"
        db.commit()
        raise


def _get_record(db: Session, user_id: str, key: str) -> IdempotencyRecord:
    return (
        db.query(IdempotencyRecord)
        .filter_by(user_id=user_id, key=key)
        .one()
    )


def _handle_existing(db, req, key, req_hash, now):
    existing = _get_record(db, req.user_id, key)

    # Same key, different request body -> client bug
    if existing.request_hash != req_hash:
        raise HTTPException(422, "Idempotency key reused with a different request")

    if existing.status == "DONE":
        return json.loads(existing.response)          # replay saved result

    if existing.status == "FAILED":
        raise HTTPException(409, "Previous attempt failed, use a new key")

    # IN_PROGRESS
    raise HTTPException(409, "Payment in progress, retry shortly")


def reconcile(db: Session) -> int:
    """Crash recovery: for records stuck IN_PROGRESS, ask the gateway what
    really happened and fix our records. Returns number of records fixed."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(seconds=STALE_IN_PROGRESS_SECONDS)
    stuck = (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.status == "IN_PROGRESS",
                IdempotencyRecord.created_at < cutoff)
        .all()
    )
    fixed = 0
    for rec in stuck:
        charge = gateway.lookup(f"{rec.user_id}:{rec.key}")
        if charge:
            rec.status = "DONE"
            rec.response = json.dumps(charge)
            if not db.query(Payment).filter_by(order_id=charge["order_id"]).first():
                db.add(Payment(
                    order_id=charge["order_id"],
                    user_id=rec.user_id,
                    amount_inr=charge["amount_inr"],
                    gateway_ref=charge["gateway_ref"],
                ))
        else:
            rec.status = "FAILED"   # gateway never charged -> safe to retry with new key
        fixed += 1
    db.commit()
    return fixed


@app.post("/admin/reconcile")
def reconcile_endpoint(db: Session = Depends(get_db)):
    return {"fixed": reconcile(db)}
