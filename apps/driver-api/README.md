# driver-api

Public REST API for drivers' location pings: the system's **firehose**. Every ping becomes a Kafka record on `driver.locations`, keyed by driver ID, which location-service consumes into the Valkey geo index.

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
KAFKA_BOOTSTRAP=localhost:9092 uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## API
**`POST /drivers/{driver_id}/location`**
```bash
curl -s -X POST localhost:8080/drivers/d1/location -H 'content-type: application/json' -d '{"lat":52.52,"lon":13.40}'
```
```json
{"status": "accepted"}
```

| Status | When |
|---|---|
| `202` | queued for Kafka |
| `422` | `driver_id` isn't 1–64 of `A-Z a-z 0-9 _ -`, or `lat`/`lon` missing or out of range |
| `503` + `Retry-After: 1` | **backpressure**: the producer's local queue is full, because Kafka is slow or down |

## Design choices worth discussing (topic 20)
- **Key = driver ID.** All pings of one driver go to the same partition, so they stay **in order**. Ordering is per partition, never across a topic.
- **Fire-and-forget.** The request doesn't wait for the broker's acknowledgement. librdkafka batches records (`KAFKA_LINGER_MS`) and delivers them in the background; failures show up in `kafka_produce_errors_total`. That's right for location pings, because the next ping replaces a lost one seconds later. rider-api makes the opposite choice for rides (it waits, through its outbox).
- **Backpressure instead of unbounded buffering.** When Kafka can't keep up, the producer's queue fills, and the API answers `503` with `Retry-After`, telling clients to slow down. The alternative, buffering forever, ends in an out-of-memory kill.
- **Idempotent producer** with `acks=all` (the default): retries can't duplicate or reorder records within a partition.

## Contracts
| | |
|---|---|
| Readiness | `/readyz`: Kafka brokers reachable and topic `driver.locations` exists |
| Logs | one `"msg": "request"` line per ping at INFO. At hundreds of pings a second, that's a lot of log volume: `LOG_LEVEL=WARNING` turns it off (and it's a cost discussion for topic 19) |
| Shutdown | SIGTERM → readiness 503, in-flight requests finish, the producer flushes its queue (up to 10 s) |

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `KAFKA_BOOTSTRAP` | (required) | |
| `KAFKA_ACKS` | `all` | `all`, `1` or `0`. Anything but `all` also turns idempotence off |
| `KAFKA_LINGER_MS` | `5` | how long to wait to fill a batch: more batching = fewer requests, a little more latency |
| `KAFKA_COMPRESSION` | `lz4` | `none`, `gzip`, `snappy`, `lz4`, `zstd` |
| `LOG_LEVEL` | `INFO` | |
| `SHUTDOWN_DELAY_SECONDS` | `0` | keep serving this long after SIGTERM |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | tracing on when set (topic 19) |

**Event `driver.locations`** (key = `driver_id`):
```json
{"driver_id": "d1", "lat": 52.52, "lon": 13.4, "ts": "2026-10-15T12:00:00.123+00:00"}
```

## Metrics
`driver_locations_accepted_total`, `driver_locations_rejected_total`, `kafka_messages_produced_total{topic}`, `kafka_produce_errors_total{topic}`, and the HTTP metrics (`http_requests_total`, `http_request_duration_seconds`). An HPA on requests per second (topic 21) uses the HTTP ones.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```
