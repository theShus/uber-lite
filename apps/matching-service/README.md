# matching-service

Kafka consumer: for every `ride.requested`, find the **nearest online, free driver** in Valkey and publish `ride.matched`.

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
KAFKA_BOOTSTRAP=localhost:9092 VALKEY_URL=redis://localhost:6379/0 uvicorn app.main:app --host 0.0.0.0 --port 8080
```
As with location-service, the worker runs inside the uvicorn process, and HTTP serves only health and metrics.

## How a match is made
1. `GEOSEARCH drivers:geo` around the pickup: 3 km first, then 10 km, nearest first, 20 candidates at a time.
2. Skip drivers without `driver:last_seen:<id>` (offline), and remove them from the index.
3. **Claim** the first free one: `SET busy:<driver> <ride_id> NX EX 60`. `NX` makes the claim atomic, so two matching replicas handling two rides at the same moment can't take the same driver.
4. Remember the match: `SET matched:<ride_id> <driver> EX 3600`.
5. Publish `ride.matched` (key = `ride_id`) and **wait for the acknowledgement** before the input offset is stored.

No driver within 10 km: the ride is logged (`no driver available`), counted in `rides_unmatched_total`, and **not** retried. In a rush hour with too few drivers, that counter climbs, and pricing's surge with it: the scaling labs make it happen on purpose.

## Idempotency (the topic 10/20 interview point)
Delivery is at-least-once, so the same `ride.requested` can arrive twice: after a crash, a rebalance, or a retry after a failed publish. Step 4 makes that harmless. A ride already in `matched:` is answered with the **same** driver, and no second driver is claimed. trip-service then ignores the duplicate `ride.matched` (its trips table is keyed by `ride_id`).

**A driver stays busy for `BUSY_TTL_SECONDS`** (60 s). This service doesn't hear when a trip ends, so the claim simply expires. Keep it close to the trip duration (trip-service's `TRIP_DURATION_S`).

## Contracts
| | |
|---|---|
| Readiness | Valkey PING, Kafka reachable with `ride.requested` and `ride.matched` present, consumer thread alive |
| Logs | `"msg": "ride matched"` with `ride_id`, `driver_id`, `distance_km`; `no driver available` warnings |
| Shutdown | as all consumers: finish the message in hand, commit, leave the group, flush the producer |

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `KAFKA_BOOTSTRAP`, `VALKEY_URL` | (required) | |
| `BUSY_TTL_SECONDS` | `60` | how long a matched driver can't be matched again |
| `PROCESSING_DELAY_MS`, `KAFKA_GROUP_PROTOCOL`, `KAFKA_ASSIGNMENT_STRATEGY` | | consumer knobs, see location-service's README |
| `KAFKA_ACKS`, `KAFKA_LINGER_MS`, `KAFKA_COMPRESSION` | | producer knobs |
| `LOG_LEVEL`, `SHUTDOWN_DELAY_SECONDS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | | as everywhere |

**Event `ride.matched`** (key = `ride_id`):
```json
{"ride_id": "…", "rider_id": "r1", "driver_id": "d17", "distance_km": 0.412,
 "pickup": {"lat": 52.52, "lon": 13.4}, "dropoff": {"lat": 52.5, "lon": 13.45},
 "price": {"amount_cents": 736, "currency": "EUR"}, "matched_at": "…"}
```
`distance_km` is the driver's distance to the pickup. It's `null` when a redelivered ride was answered from `matched:`.

## Metrics
`rides_matched_total`, `rides_unmatched_total`, `rides_rematched_total`, plus the Kafka consumer and producer metrics.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```
