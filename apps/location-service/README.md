# location-service

Kafka consumer that turns the `driver.locations` firehose into a **live geo index** in Valkey. matching-service and pricing-service query that index. This is the service KEDA scales on consumer lag (topic 21).

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
KAFKA_BOOTSTRAP=localhost:9092 VALKEY_URL=redis://localhost:6379/0 uvicorn app.main:app --host 0.0.0.0 --port 8080
```
The worker command **is** the uvicorn command. The HTTP port serves only `/healthz`, `/readyz` and `/metrics`; the consumer runs in a background thread of the same process. Use the same Dockerfile pattern as the APIs.

## What it does
For each record on `driver.locations` (consumer group **`location-service`**):
```text
GEOADD drivers:geo <lon> <lat> <driver_id>         position (one entry per driver; the newest wins)
SET    driver:last_seen:<driver_id> <ts> EX 60     "online" marker: matching ignores drivers without one
```
Try it: `valkey-cli GEOSEARCH drivers:geo FROMLONLAT 13.40 52.52 BYRADIUS 3 km ASC COUNT 5 WITHDIST`.

## Delivery semantics (all consumers share them: `app/core/kafka.py`)
- **At-least-once.** An offset is stored only **after** the message was processed, and auto-commit commits stored offsets every few seconds. A crash replays the last few seconds, which is harmless here: writing the same position twice changes nothing.
- **A failing message is retried**, not skipped. The worker seeks back to it and retries with backoff (0.5 s, doubling, max 5 s). Polling continues, so the consumer stays in its group, but that partition **stalls**, lag grows, and nothing is lost. Valkey down for a minute = a lag spike, then catch-up.
- **A poison message** (not JSON, missing fields, a position GEO can't store) is logged and skipped, counted in `kafka_messages_consumed_total{result="dropped"}`. Retrying it would block its partition forever. A real system would also copy it to a dead-letter topic (topic 20).
- **Ordering:** records are keyed by driver ID, so one driver's pings arrive in order on one partition.

## Scaling notes (topics 20–21)
- `driver.locations` has **12 partitions**, so at most **12** consumers in the group do work; a 13th sits idle.
- Every scale-up or scale-down is a **rebalance**. The default protocol is `classic` with `cooperative-sticky` assignment (only moved partitions pause). `KAFKA_GROUP_PROTOCOL=consumer` switches to **KIP-848**, where the broker computes assignments. The logs show each `partitions assigned` / `partitions revoked`.
- **`PROCESSING_DELAY_MS`** slows every message down on purpose, so you can create lag on demand and watch KEDA react.

## Contracts
| | |
|---|---|
| Readiness | Valkey answers PING, Kafka is reachable with the topic present, and the consumer thread is alive |
| Logs | JSON; partition assignments and revocations; warnings for retries; errors for dropped messages |
| Shutdown | SIGTERM → readiness 503, the worker finishes its message, commits, and **leaves the group** (so the others rebalance at once, instead of waiting for a session timeout) |

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `KAFKA_BOOTSTRAP` | (required) | |
| `VALKEY_URL` | (required) | |
| `DRIVER_TTL_SECONDS` | `60` | how long a driver counts as online after a ping |
| `PROCESSING_DELAY_MS` | `0` | artificial slowness per message |
| `KAFKA_GROUP_PROTOCOL` | `classic` | or `consumer` (KIP-848) |
| `KAFKA_ASSIGNMENT_STRATEGY` | `cooperative-sticky` | classic protocol only: `range`, `roundrobin`, `cooperative-sticky` |
| `KAFKA_SESSION_TIMEOUT_MS` | `45000` | classic protocol only |
| `KAFKA_MAX_POLL_INTERVAL_MS` | `300000` | |
| `LOG_LEVEL`, `SHUTDOWN_DELAY_SECONDS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | | as everywhere |

## Metrics
`location_updates_total`, `kafka_messages_consumed_total{topic,result}`, `kafka_message_processing_seconds{topic}`. Lag itself is measured outside the app, by KEDA or a Kafka exporter, from the group's committed offsets.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```
`tests/test_consumer_worker.py` exercises the shared consumer loop with a fake Kafka consumer (retry, poison, offset storing, protocol config). `tests/test_handler.py` runs the handler against an in-memory Valkey.
