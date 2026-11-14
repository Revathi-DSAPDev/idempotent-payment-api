import datetime as dt

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from .database import Base


class IdempotencyRecord(Base):
    """One row per payment attempt. The unique constraint is what makes the
    claim atomic: only one request can ever insert a given (user_id, key)."""

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_key"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False)
    key = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    status = Column(String, nullable=False)  # IN_PROGRESS | DONE | FAILED
    response = Column(Text, nullable=True)   # JSON string of saved response
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class Payment(Base):
    """Actual charges. Unique order_id means at most one charge per order,
    even across different clients (app vs web)."""

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("order_id", name="uq_order"),)

    id = Column(Integer, primary_key=True)
    order_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    amount_inr = Column(Integer, nullable=False)
    gateway_ref = Column(String, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
