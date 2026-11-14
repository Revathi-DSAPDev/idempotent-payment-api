"""Fake payment gateway. Like Razorpay/Stripe, it also honours the
idempotency key, so a crash between 'charge' and 'save result' cannot
cause a second charge."""
import threading
import uuid

_lock = threading.Lock()
_charges: dict[str, dict] = {}   # idempotency key -> charge
charge_call_count = 0            # used by tests


def charge(key: str, amount_inr: int, order_id: str) -> dict:
    global charge_call_count
    with _lock:
        if key in _charges:                 # gateway-level dedupe
            return _charges[key]
        charge_call_count += 1
        result = {
            "gateway_ref": f"gw_{uuid.uuid4().hex[:12]}",
            "status": "SUCCESS",
            "amount_inr": amount_inr,
            "order_id": order_id,
        }
        _charges[key] = result
        return result


def lookup(key: str) -> dict | None:
    """Used by reconciliation: what really happened for this key?"""
    with _lock:
        return _charges.get(key)


def reset():
    global charge_call_count
    with _lock:
        _charges.clear()
        charge_call_count = 0
