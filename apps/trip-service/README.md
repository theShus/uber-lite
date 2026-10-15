# trip-service

REST + consumer. Turns each `ride.matched` into a **trip**, "drives" it for `TRIP_DURATION_S` seconds, then publishes `trip.completed`. It notifies the rider and the driver over NATS JetStream along the way.

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export DATABASE_URL=postgresql://trips:trips@localhost:5432/trips KAFKA_BOOTSTRAP=localhost:9092 NATS_URL=nats://localhost:4222
python -m app.migrate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## What happens to a ride
1. **`ride.matched`** (consumer group **`trip-service`**): `INSERT INTO trips … ON CONFLICT (ride_id) DO NOTHING` with `status = 'started'` and `completes_at = now() + TRIP_DURATION_S`. A duplicate event inserts nothing and is ignored (idempotent on `ride_id`).
2. NATS: `notify.rider.<rider_id>` ("Your driver is on the way") and `notify.driver.<driver_id>` ("New ride").
3. **The completion scheduler**, a loop in every replica, every `COMPLETION_POLL_SECONDS`, in one transaction:
   ```sql
   SELECT … FROM trips WHERE status = 'started' AND completes_at <= now()
   ORDER BY completes_at LIMIT 50 FOR UPDATE SKIP LOCKED
   ```
   then publishes **`trip.completed`** for those rows (waiting for Kafka's acknowledgement), and marks them `completed`.
4. NATS: `notify.rider.<rider_id>` ("Trip completed: 7.36 EUR").

**`FOR UPDATE SKIP LOCKED`** is the classic "Postgres as a job queue" pattern. Each replica locks the rows it takes, and the others skip them instead of waiting. Three replicas complete trips in parallel, and none completes the same trip twice. If Kafka is down, the transaction rolls back, the trips stay `started`, and the next tick retries. At-least-once again: payment-service is idempotent on `ride_id`.

**Notifications are best effort.** A NATS outage is logged and counted (`trip_notifications_failed_total`), but never stops a trip. That's a deliberate choice: the trip and the payment matter more than the push message.

## The JetStream stream and consumer
At startup trip-service makes sure these exist (idempotently):
- stream **`NOTIFY`**: subjects `notify.>`, file storage, messages kept 24 h
- durable consumer **`notification-service`**: explicit acks, `ack_wait` 30 s, `max_deliver` 5

**Why the producer creates the consumer:** with KEDA scaling notification-service **to zero** (topic 21), no notification-service pod may exist to create it, and KEDA needs the consumer to read its pending count. notification-service declares the same consumer with the same settings; change both together.

## API
**`GET /trips/{ride_id}`**: the trip, `404` if unknown, `422` if not a UUID.
```json
{"ride_id": "…", "rider_id": "r1", "driver_id": "d7", "amount_cents": 736, "currency": "EUR",
 "status": "completed", "started_at": "…", "completes_at": "…", "completed_at": "…"}
```

## Contracts
| | |
|---|---|
| Readiness | Postgres, NATS connected, Kafka reachable with `ride.matched` and `trip.completed`, consumer thread alive |
| Logs | `trip started`, `trip completed` (with `ride_id`), `duplicate ride.matched ignored`, warnings for failed notifications |
| Shutdown | the consumer finishes its message and leaves the group, the scheduler finishes its batch, the producer flushes, NATS drains |
| Migrations | `python -m app.migrate` (versioned, advisory-locked), like rider-api |

**Inside the process:** the Kafka consumer runs in its own thread, and hands each message to the event loop, where the database pool and the NATS connection live, waiting for the result. An exception means "retry this message". One pool and one NATS connection serve the consumer, the scheduler and the API.

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL`, `KAFKA_BOOTSTRAP`, `NATS_URL` | (required) | |
| `TRIP_DURATION_S` | `20` | how long a simulated drive takes. Keep matching's `BUSY_TTL_SECONDS` above it |
| `COMPLETION_POLL_SECONDS` | `1` | how often the scheduler looks for due trips |
| `DB_POOL_MAX` | `10` | Postgres connections per pod |
| consumer and producer knobs | | see location-service's and driver-api's READMEs |
| `LOG_LEVEL`, `SHUTDOWN_DELAY_SECONDS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | | as everywhere |

## Data
Database `trips`, table `trips` (`app/migrations.py`), with a partial index for the scheduler's query (`WHERE status = 'started'`).

**Event `trip.completed`** (key = `ride_id`):
```json
{"ride_id": "…", "rider_id": "r1", "driver_id": "d7", "price": {"amount_cents": 736, "currency": "EUR"}, "completed_at": "…"}
```

## Metrics
`trips_started_total`, `trips_completed_total`, `trips_duplicate_matches_total`, `trip_notifications_failed_total`, plus the Kafka consumer/producer and HTTP metrics.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```
Fakes stand in for Postgres, Kafka and NATS. The NATS stream and consumer setup was also checked against a real `nats-server -js` (2.15.1) when the service was written.
