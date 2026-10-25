# trip-service

REST + consumer. Turns each `ride.matched` into a **trip**, "drives" it for `TRIP_DURATION_S` seconds, then publishes `trip.completed`. It notifies the rider and the driver along the way, through Kafka (`notification.requested`, read by notification-service).

> **Changed 2026-10-25:** notifications used to go to NATS JetStream (`notify.rider.<id>`, stream `NOTIFY`, which trip-service also created). They're now one more Kafka topic. Why: [docs/app-reference.md, History](../../docs/app-reference.md#history).

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export DATABASE_URL=postgresql://trips:trips@localhost:5432/trips KAFKA_BOOTSTRAP=localhost:9092
python -m app.migrate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## What happens to a ride
1. **`ride.matched`** (consumer group **`trip-service`**): `INSERT INTO trips … ON CONFLICT (ride_id) DO NOTHING` with `status = 'started'` and `completes_at = now() + TRIP_DURATION_S`. A duplicate event inserts nothing and is ignored (idempotent on `ride_id`).
2. **`notification.requested`**, twice: key `rider:<rider_id>` ("Your driver is on the way") and key `driver:<driver_id>` ("New ride").
3. **The completion scheduler**, a loop in every replica, every `COMPLETION_POLL_SECONDS`, in one transaction:
   ```sql
   SELECT … FROM trips WHERE status = 'started' AND completes_at <= now()
   ORDER BY completes_at LIMIT 50 FOR UPDATE SKIP LOCKED
   ```
   then publishes **`trip.completed`** for those rows (waiting for Kafka's acknowledgement), and marks them `completed`.
4. **`notification.requested`**: key `rider:<rider_id>` ("Trip completed: 7.36 EUR").

**`FOR UPDATE SKIP LOCKED`** is the classic "Postgres as a job queue" pattern. Each replica locks the rows it takes, and the others skip them instead of waiting. Three replicas complete trips in parallel, and none completes the same trip twice. If Kafka is down, the transaction rolls back, the trips stay `started`, and the next tick retries. At-least-once again: payment-service is idempotent on `ride_id`.

**Notifications are best effort.** Each one waits up to 5 s for Kafka's acknowledgement. A failure is logged and counted (`trip_notifications_failed_total`), but never stops a trip. That's a deliberate choice: the trip and the payment matter more than the push message.

**Why the key is `<kind>:<id>`:** one recipient's notifications share a partition, so "driver on the way" always arrives before "trip completed", and a rider and a driver with the same id don't collide.

## API
**`GET /trips/{ride_id}`**: the trip, `404` if unknown, `422` if not a UUID.
```json
{"ride_id": "…", "rider_id": "r1", "driver_id": "d7", "amount_cents": 736, "currency": "EUR",
 "status": "completed", "started_at": "…", "completes_at": "…", "completed_at": "…"}
```

## Contracts
| | |
|---|---|
| Readiness | Postgres, Kafka reachable with `ride.matched`, `trip.completed` and `notification.requested`, consumer thread alive |
| Logs | `trip started`, `trip completed` (with `ride_id`), `duplicate ride.matched ignored`, warnings for failed notifications |
| Shutdown | the consumer finishes its message and leaves the group, the scheduler finishes its batch, the producer flushes |
| Migrations | `python -m app.migrate` (versioned, advisory-locked), like rider-api |

**Inside the process:** the Kafka consumer runs in its own thread, and hands each message to the event loop, where the database pool lives, waiting for the result. An exception means "retry this message". One pool and one Kafka producer serve the consumer, the scheduler and the API.

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL`, `KAFKA_BOOTSTRAP` | (required) | |
| `TRIP_DURATION_S` | `20` | how long a simulated drive takes. Keep matching's `BUSY_TTL_SECONDS` above it |
| `COMPLETION_POLL_SECONDS` | `1` | how often the scheduler looks for due trips |
| `DB_POOL_MAX`, `DB_POOL_MIN` | `10`, `1` | Postgres connections per pod: at most, and kept open from startup (rider-api's README) |
| consumer and producer knobs | | see location-service's and driver-api's READMEs |
| `LOG_LEVEL`, `SHUTDOWN_DELAY_SECONDS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | | as everywhere |

## Data
Database `trips`, table `trips` (`app/migrations.py`), with a partial index for the scheduler's query (`WHERE status = 'started'`).

**Event `trip.completed`** (key = `ride_id`):
```json
{"ride_id": "…", "rider_id": "r1", "driver_id": "d7", "price": {"amount_cents": 736, "currency": "EUR"}, "completed_at": "…"}
```

**Event `notification.requested`** (key = `<kind>:<id>`, e.g. `rider:r1`):
```json
{"kind": "rider", "target": "r1", "ride_id": "…", "message": "Your driver is on the way"}
```

## Metrics
`trips_started_total`, `trips_completed_total`, `trips_duplicate_matches_total`, `trip_notifications_failed_total`, plus the Kafka consumer/producer and HTTP metrics.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```
Fakes stand in for Postgres and Kafka. The whole notification path (`ride.matched` → trip → `notification.requested` → notification-service, and the completion notification) was checked against a real Kafka 4.1.0 and Postgres 17 on 2026-10-25.

Against a **real Postgres** (skipped unless `PG_TEST_URL` is set):
```bash
PG_TEST_URL=postgresql://trips:trips@localhost:5432/trips pytest -q tests/test_store_pg.py
```
They check that a duplicate `ride.matched` starts no second trip, that a failed publish leaves the trip `started`, and that two schedulers split the due trips between them (`FOR UPDATE SKIP LOCKED`). Verified against PostgreSQL 18.6.
