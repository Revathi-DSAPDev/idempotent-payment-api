# idempotent-payment-api
FastAPI payment service that prevents duplicate charges using idempotency keys, atomic DB claims, and crash-safe reconciliation.
# Idempotent Payment API

A FastAPI payment service that prevents **duplicate charges** from double taps, network retries, parallel requests, and server crashes.

> Core idea: at-least-once delivery + idempotent processing = effectively-once outcome.

## How it works

1. The client sends a unique `Idempotency-Key` header with each payment attempt.
2. The server atomically claims the key (unique DB constraint on `user_id + key`).
3. The winner charges the gateway once, saves the result, and marks the record `DONE`.
4. Repeat requests with the same key get the saved response replayed, never a second charge.
5. A reconciliation job fixes records left `IN_PROGRESS` after a crash by checking the gateway.

## Edge cases handled

- Same key twice: one charge, same response
- 8 parallel identical requests: exactly one charge
- Same key with a different amount: 422
- Missing key: 422
- Same order from app and web (different keys): second gets 409
- Crash after charge: reconciliation marks it DONE
- Crash before charge: reconciliation marks it FAILED so the client can retry

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest -v
```

## Limitations

The gateway is simulated and SQLite is used for simplicity. A real deployment would use Razorpay or Stripe and PostgreSQL.
