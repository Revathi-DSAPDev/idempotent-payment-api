import datetime as dt
import threading

import pytest
from fastapi.testclient import TestClient

from app import gateway
from app.database import Base, SessionLocal, engine
from app.main import app, reconcile
from app.models import IdempotencyRecord, Payment

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    gateway.reset()
    yield


def body(order="order_8842", amount=500, user="user_1"):
    return {"user_id": user, "order_id": order, "amount_inr": amount}


def pay(key, **kw):
    return client.post("/pay", json=body(**kw), headers={"Idempotency-Key": key})


def test_same_key_twice_charges_once_and_replays_result():
    r1 = pay("k1")
    r2 = pay("k1")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    assert gateway.charge_call_count == 1


def test_parallel_identical_requests_charge_once():
    results = []

    def worker():
        results.append(pay("k_parallel").status_code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert gateway.charge_call_count == 1
    assert results.count(200) >= 1
    assert all(code in (200, 409) for code in results)
    db = SessionLocal()
    assert db.query(Payment).count() == 1
    db.close()


def test_same_key_different_amount_is_rejected():
    assert pay("k2", amount=500).status_code == 200
    r = pay("k2", amount=900)
    assert r.status_code == 422
    assert gateway.charge_call_count == 1


def test_missing_key_is_rejected():
    r = client.post("/pay", json=body())
    assert r.status_code == 422


def test_different_user_same_key_does_not_collide():
    assert pay("shared", user="alice", order="o1").status_code == 200
    assert pay("shared", user="bob", order="o2").status_code == 200
    assert gateway.charge_call_count == 2


def test_same_order_different_key_blocked_across_clients():
    """Mobile app and website generate different keys for the same order."""
    assert pay("app_key", order="o9").status_code == 200
    r = pay("web_key", order="o9")
    assert r.status_code == 409
    db = SessionLocal()
    assert db.query(Payment).count() == 1
    db.close()


def test_crash_after_charge_is_fixed_by_reconciliation():
    """Simulate: gateway charged, server crashed before saving the result."""
    db = SessionLocal()
    old = dt.datetime.utcnow() - dt.timedelta(minutes=5)
    db.add(IdempotencyRecord(
        user_id="user_1", key="k_crash", request_hash="x",
        status="IN_PROGRESS", created_at=old,
        expires_at=old + dt.timedelta(hours=24),
    ))
    db.commit()
    gateway.charge("user_1:k_crash", 500, "order_crash")

    assert reconcile(db) == 1
    rec = db.query(IdempotencyRecord).filter_by(key="k_crash").one()
    assert rec.status == "DONE"
    assert db.query(Payment).filter_by(order_id="order_crash").count() == 1
    assert gateway.charge_call_count == 1
    db.close()


def test_crash_before_charge_marks_failed_so_client_can_retry():
    db = SessionLocal()
    old = dt.datetime.utcnow() - dt.timedelta(minutes=5)
    db.add(IdempotencyRecord(
        user_id="user_1", key="k_nocharge", request_hash="x",
        status="IN_PROGRESS", created_at=old,
        expires_at=old + dt.timedelta(hours=24),
    ))
    db.commit()
    assert reconcile(db) == 1
    rec = db.query(IdempotencyRecord).filter_by(key="k_nocharge").one()
    assert rec.status == "FAILED"
    db.close()
