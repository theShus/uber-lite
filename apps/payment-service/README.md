# payment-service

Kafka consumer: for every `trip.completed`, charge the rider **exactly once per ride**, through a fake payment provider that's slow and failing on purpose, then publish `payment.settled`.

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export DATABASE_URL=postgresql://payments:payments@localhost:5432/payments KAFKA_BOOTSTRAP=localhost:9092
python -m app.migrate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```
The consumer runs inside the uvicorn process; HTTP serves only health and metrics.

## How a payment works (consumer group `payment-service`)
1. `INSERT INTO payments … 'pending' ON CONFLICT (ride_id) DO NOTHING`, then read the row.
2. **Already `charged` or `failed`?** It's a redelivered `trip.completed`: **don't charge again**. Log `duplicate trip.completed, already settled`, and re-send the settlement.
3. Otherwise charge, with `ride_id` as the **idempotency key**, up to `PSP_MAX_ATTEMPTS` (3), with backoff 0.2 s → 0.4 s between attempts. Each failure logs `charge failed, retrying`.
4. Success: `status = 'charged'`, log `charged`. Three failures: `status = 'failed'`, log `payment failed`.
5. Publish `payment.settled`, waiting for Kafka's acknowledgement, before the input offset is stored.

## Idempotency, twice
- **In our database:** `ride_id` is the primary key, so a ride can have one payment row, whatever Kafka redelivers.
- **At the provider:** a crash between "the provider charged" and "we wrote `charged`" leaves a `pending` row. The redelivered event charges again, with the **same idempotency key**, and real providers (Stripe, Adyen…) return the original charge instead of a new one. The fake provider does the same: its reference is derived from the key.

Both are needed. The first protects against duplicate events, the second against our own crashes. This is the answer to "how do you make sure a customer isn't charged twice?" (topic 10.1, topic 20).

## The failure knobs (topic 19)
`PSP_FAILURE_RATE` and `PSP_LATENCY_MS` change the provider's behaviour **without a deploy**: just the environment. Raise the failure rate to `0.3` and watch:
- `payments_total{result="failed"}` climb
- `psp_request_duration_seconds` show the retries
- an SLO on successful payments burn its error budget, and its alert fire

## Contracts
| | |
|---|---|
| Readiness | Postgres reachable, Kafka reachable with `trip.completed` and `payment.settled`, consumer thread alive |
| Logs | `charged`, `charge failed, retrying`, `payment failed`, `duplicate …`, all with `ride_id` |
| Shutdown | finish the payment in progress, commit, leave the group, flush the producer |
| Migrations | `python -m app.migrate` |

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL`, `KAFKA_BOOTSTRAP` | (required) | |
| `PSP_FAILURE_RATE` | `0.02` | share of provider calls that fail |
| `PSP_LATENCY_MS` | `150` | mean provider latency (±30%) |
| `PSP_MAX_ATTEMPTS` | `3` | attempts before a payment is `failed` |
| `DB_POOL_MAX` | `10` | |
| consumer and producer knobs | | see location-service's and driver-api's READMEs |
| `LOG_LEVEL`, `SHUTDOWN_DELAY_SECONDS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | | as everywhere |

## Data
Database `payments`, table `payments`: ride_id (PK), amount_cents, currency, status (`pending`/`charged`/`failed`), attempts, psp_reference, created_at, updated_at.

**Event `payment.settled`** (key = `ride_id`; nobody consumes it yet):
```json
{"ride_id": "…", "status": "charged", "amount_cents": 736, "currency": "EUR",
 "psp_reference": "psp_3f2a…", "attempts": 1, "settled_at": "…"}
```

## Metrics
`payments_total{result}` (charged, failed, duplicate), `psp_request_duration_seconds{result}` (ok, error), plus the Kafka consumer/producer metrics. A payment can take up to ~1 s (latency × attempts + backoff), which makes this the consumer whose **lag** grows first in a rush hour: KEDA's best demo (topic 21).

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```
