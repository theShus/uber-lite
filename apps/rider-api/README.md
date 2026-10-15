# rider-api

Public REST API for riders. A ride request is priced (gRPC to pricing-service), stored in Postgres **together with its event** (a transactional outbox), and published to Kafka as `ride.requested`.

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export DATABASE_URL=postgresql://rides:rides@localhost:5432/rides KAFKA_BOOTSTRAP=localhost:9092 PRICING_ADDR=localhost:50051
python -m app.migrate                       # create/update the tables, then exit
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## API
**`POST /rides`**: request a ride.
```bash
curl -s -X POST localhost:8080/rides -H 'content-type: application/json' \
  -d '{"rider_id":"r1","pickup":{"lat":52.52,"lon":13.40},"dropoff":{"lat":52.50,"lon":13.45}}'
```
```json
{"ride_id": "5b1c…", "status": "requested", "price": {"amount_cents": 736, "currency": "EUR"}, "surge": 1.0}
```

| Status | When |
|---|---|
| `202` | priced and stored. The `ride.requested` event follows from the outbox within moments |
| `422` | invalid body: `rider_id` 1–64 chars, `lat` −90…90, `lon` −180…180, both points required |
| `503` | pricing-service or Postgres unavailable. Nothing was stored; retrying is safe |

**`GET /rides/{ride_id}`**: the stored ride (`404` if unknown, `422` if not a UUID). `GET /rides` is `405`: the collection only accepts POST.

## How a request flows
1. `Quote` over gRPC, with a 2-second deadline.
2. **One transaction:** `INSERT INTO rides …` and `INSERT INTO outbox …` (the event).
3. `202` to the client.
4. The **outbox relay** (a background task in every replica) reads pending events with `FOR UPDATE SKIP LOCKED`, publishes them to Kafka (key = `ride_id`, waiting for the broker's acknowledgement), and deletes them, in one transaction.

**Why an outbox:** "write to the database, then publish to Kafka" from the request handler can't be atomic. A crash between the two loses the event, or publishes an event for a ride that was rolled back. Here the event is part of the ride's transaction. Publishing is **at-least-once**: if the relay crashes after Kafka acknowledged and before its delete commits, the event goes out again. That's why matching-service is idempotent per `ride_id`. With Kafka down, rides are still accepted, `outbox_pending_events` grows, and the backlog drains when Kafka is back.

## Contracts
| | |
|---|---|
| Readiness | `/readyz` checks **Postgres** and **Kafka** (brokers reachable, topic `ride.requested` exists). **Not pricing:** a pricing outage fails ride requests fast with `503`, instead of making every rider-api pod unready at once, which would turn a partial outage into a total one (09.4 §6, 11.8 §5) |
| Logs | JSON; `"msg": "ride requested"` with `ride_id`, `rider_id`, `amount_cents`, `surge`; one `"msg": "request"` line per HTTP request (not for probes and `/metrics`) |
| Shutdown | SIGTERM → readiness 503, uvicorn drains in-flight requests, the relay finishes its batch, the Kafka producer flushes |
| Migrations | `python -m app.migrate`: versioned SQL in `app/migrations.py`, tracked in `schema_migrations`, under a Postgres advisory lock so two migration Jobs never run at once |

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | (required) | its own database: `rides` |
| `KAFKA_BOOTSTRAP` | (required) | |
| `PRICING_ADDR` | (required) | `pricing-service:50051` (one connection through the ClusterIP), or `dns:///pricing-service-headless.uber-lite.svc.cluster.local:50051` (round-robin across pods, 16.3) |
| `DB_POOL_MAX` | `10` | Postgres connections per pod. Pods × this must stay below the server's `max_connections` (topic 17) |
| `KAFKA_ACKS`, `KAFKA_LINGER_MS`, `KAFKA_COMPRESSION` | `all`, `5`, `lz4` | producer settings (topic 20) |
| `LOG_LEVEL` | `INFO` | |
| `SHUTDOWN_DELAY_SECONDS` | `0` | keep serving this long after SIGTERM |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | tracing on when set (topic 19) |

## Data
Database `rides`:
- `rides`: id (uuid), rider_id, pickup/dropoff lat/lon, amount_cents, currency, surge, quote_id, status, created_at
- `outbox`: id, topic, key, payload (jsonb), created_at. Normally empty or nearly empty.

**Event `ride.requested`** (key = `ride_id`):
```json
{"ride_id": "…", "rider_id": "r1", "pickup": {"lat": 52.52, "lon": 13.4}, "dropoff": {"lat": 52.5, "lon": 13.45},
 "price": {"amount_cents": 736, "currency": "EUR"}, "surge": 1.0, "requested_at": "2026-10-15T12:00:00+00:00"}
```

## Metrics
- `rides_requested_total`, `rides_rejected_total{reason}`, `outbox_pending_events`
- `pricing_client_errors_total{code}`, `kafka_messages_produced_total{topic}`, `kafka_produce_errors_total{topic}`
- `http_requests_total{method,route,status}`, `http_request_duration_seconds{method,route}`. Labels use the route **template** (`/rides/{ride_id}`), never the raw path.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```
In-memory fakes stand in for Postgres, pricing and Kafka. The tests cover the outbox behaviour (Kafka down → the event waits → delivered when it's back), the error codes, and readiness.

Against a **real Postgres** (skipped unless `PG_TEST_URL` is set; the user must be allowed to own the database):
```bash
PG_TEST_URL=postgresql://rides:rides@localhost:5432/rides pytest -q tests/test_store_pg.py
```
They run the migrations, then check the real SQL: a ride and its outbox row commit together, a Kafka failure rolls the relay back, and two relays never send the same row (`FOR UPDATE SKIP LOCKED`). Verified against PostgreSQL 18.6.

`app/proto/` is rider-api's copy of pricing-service's contract. See pricing-service's README to regenerate it.
