# Uber-lite: application reference

The Python services in `apps/`, in one place: who talks to whom, every topic, key and table, and the knobs the labs turn. Each service's own `README.md` has the details. Written with the app (2026-10-15).

**What's here and what isn't:** `apps/<service>/` holds the code, its tests, `requirements*.txt`, a `pyproject.toml` (pytest and ruff settings) and a README. Everything around the code is built in the labs: Dockerfiles, Compose, manifests, the Helm chart, CI, infrastructure, GitOps.

## Services
| Service | Kind | Ports | Consumes | Produces | State |
|---|---|---|---|---|---|
| rider-api | public REST | 8080 | (gRPC → pricing) | `ride.requested` (via outbox) | Postgres `rides` |
| driver-api | public REST | 8080 | | `driver.locations` | |
| location-service | Kafka consumer | 8080 (health) | `driver.locations` | | Valkey `drivers:geo`, `driver:last_seen:*` |
| matching-service | Kafka consumer | 8080 (health) | `ride.requested` | `ride.matched` | Valkey `busy:*`, `matched:*` |
| pricing-service | internal gRPC | **50051** gRPC, 8080 (health) | | | Valkey `demand:cell:*:*` (reads `drivers:geo`) |
| trip-service | REST + consumer | 8080 | `ride.matched` | `trip.completed`, NATS `notify.*` | Postgres `trips` |
| payment-service | Kafka consumer | 8080 (health) | `trip.completed` | `payment.settled` | Postgres `payments` |
| notification-service | NATS consumer | 8080 (health) | NATS `notify.>` | | |
| simulator | CLI / Job | none | | HTTP → rider-api, driver-api | |

```mermaid
flowchart LR
  sim["simulator"] -->|"POST /rides"| rider["rider-api"]
  sim -->|"POST /drivers/{id}/location"| driver["driver-api"]
  rider -->|"gRPC Quote"| pricing["pricing-service"]
  rider -->|"outbox → ride.requested"| k[("Kafka")]
  driver -->|"driver.locations"| k
  k --> location["location-service"]
  k --> matching["matching-service"]
  location -->|"GEOADD"| v[("Valkey")]
  matching -->|"GEOSEARCH, SET NX"| v
  pricing -->|"INCR demand, GEOSEARCH"| v
  matching -->|"ride.matched"| k
  k --> trip["trip-service"]
  trip -->|"trip.completed"| k
  k --> payment["payment-service"]
  payment -->|"payment.settled"| k
  trip -->|"notify.rider/driver.*"| n[("NATS JetStream<br/>stream NOTIFY")]
  n --> notify["notification-service"]
  rider --- pg[("Postgres<br/>rides · trips · payments")]
  trip --- pg
  payment --- pg
```

## Shared contracts
| | |
|---|---|
| HTTP | port **8080**: `/healthz` (liveness, never checks dependencies), `/readyz` (dependencies + not shutting down; JSON with each check's result), `/metrics` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port 8080 --timeout-graceful-shutdown 20` for every service except the simulator (`python -m app.simulate …`). Consumers run inside that process. |
| Migrations | `python -m app.migrate` in rider-api, trip-service, payment-service: versioned SQL, a `schema_migrations` table, a Postgres advisory lock |
| Logs | JSON lines on stdout: `ts`, `level`, `service`, `msg`, plus fields (`ride_id`, `driver_id`…), and `trace_id` when tracing is on. One `"msg": "request"` line per HTTP request, except probes and `/metrics` |
| Shutdown | SIGTERM → `/readyz` 503 → in-flight work finishes → consumers commit and leave their group → producers flush → exit within 20 s |
| Config | environment variables only |
| Delivery | Kafka: at-least-once everywhere. Offsets are stored after processing; a failing message is retried (seek back, backoff); a poison message is logged and skipped. **Every consumer is idempotent:** on `ride_id` for matching, trip and payment; location-service's GEOADD simply overwrites |
| Python | 3.13. All dependencies pinned, with wheels for linux/amd64 and linux/arm64 |

## Kafka
| Topic | Partitions | Key | Producer | Consumer group |
|---|---|---|---|---|
| `driver.locations` | 12 | driver_id | driver-api (fire-and-forget) | `location-service` |
| `ride.requested` | 6 | ride_id | rider-api (transactional outbox) | `matching-service` |
| `ride.matched` | 6 | ride_id | matching-service | `trip-service` |
| `trip.completed` | 6 | ride_id | trip-service (scheduler) | `payment-service` |
| `payment.settled` | 3 | ride_id | payment-service | (none yet) |

Topics must exist beforehand: the services don't create them, and readiness reports any that are missing.

## Valkey keys
| Key | Type | Written by | Read by | TTL |
|---|---|---|---|---|
| `drivers:geo` | GEO set | location-service | matching, pricing | none (offline drivers are pruned by matching) |
| `driver:last_seen:<driver_id>` | string | location-service | matching | 60 s (`DRIVER_TTL_SECONDS`) |
| `busy:<driver_id>` | string = ride_id | matching (`SET NX`) | matching | 60 s (`BUSY_TTL_SECONDS`) |
| `matched:<ride_id>` | string = driver_id | matching | matching (redeliveries) | 1 h |
| `demand:cell:<i>:<j>` | counter (one per 0.05° grid cell: `floor(lat/0.05)`, `floor(lon/0.05)`) | pricing | pricing | 300 s window |

## NATS JetStream
- stream **`NOTIFY`**: subjects `notify.>`, file storage, 24 h retention
- durable pull consumer **`notification-service`**: explicit ack, ack wait 30 s, max deliver 5
- **both** trip-service and notification-service declare them, so KEDA can scale notification-service from zero

## Postgres
One server, a database and a user per service (database per service): `rides`, `trips`, `payments`. Tables are in each service's `app/migrations.py`. Each service connects only to its own database (`DATABASE_URL`).

## Environment variables
| Variable | Used by | Example / default |
|---|---|---|
| `KAFKA_BOOTSTRAP` | rider, driver, location, matching, trip, payment | `kafka:9092` |
| `DATABASE_URL` | rider, trip, payment | `postgresql://rides:rides@postgres:5432/rides` |
| `VALKEY_URL` | location, matching, pricing | `redis://valkey:6379/0` |
| `NATS_URL` | trip, notification | `nats://nats:4222` |
| `PRICING_ADDR` | rider-api | `pricing-service:50051`, or `dns:///pricing-service-headless…:50051` |
| `RIDER_API`, `DRIVER_API`, `RIDER_API_KEY` | simulator | `http://rider-api`, `http://driver-api`, unset |
| `LOG_LEVEL` | all | `INFO` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | all except the simulator | unset (tracing off) |
| `SHUTDOWN_DELAY_SECONDS` | all servers | `0` |

## Knobs the labs turn
| Knob | Where | Use |
|---|---|---|
| `PROCESSING_DELAY_MS` | all consumers | slow consumers down → lag → KEDA scales (topic 21) |
| `KAFKA_GROUP_PROTOCOL` = `classic` / `consumer` | all Kafka consumers | classic rebalancing vs KIP-848 (topic 20) |
| `KAFKA_ASSIGNMENT_STRATEGY` | all Kafka consumers | `range`, `roundrobin`, `cooperative-sticky` (topic 20) |
| `KAFKA_ACKS`, `KAFKA_LINGER_MS`, `KAFKA_COMPRESSION` | all producers | durability and batching (topic 20) |
| `PSP_FAILURE_RATE`, `PSP_LATENCY_MS` | payment-service | failures and latency → SLOs and alerts (topic 19) |
| `TRIP_DURATION_S`, `BUSY_TTL_SECONDS` | trip, matching | how long drivers are tied up → unmatched rides at rush hour |
| `--rush-hour`, `--drivers`, `--riders` | simulator | load |
| `service.headless` + `PRICING_ADDR=dns:///…` | chart + rider-api | gRPC balancing (16.3) |

## Left for you on purpose
- **Trace propagation through Kafka and NATS** (topic 19): HTTP, gRPC and database calls are traced when OTel is on, but no trace context travels inside messages, so a trace stops at each producer. Adding it (inject `traceparent` into message headers, extract it in the consumers) is your code change.
- Dockerfiles, `.dockerignore`, Compose, manifests, the chart, CI, infrastructure, GitOps.

## Working on the code
```bash
cd apps/<service>
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check . && ruff format --check .
```
- **`app/core/` is the same in every service:** logging, metrics, health, shutdown, Kafka, Postgres, tracing. It's copied, not shared as a package, so each service builds from its own folder alone. Change it in one service, copy it to the others, and check with `diff -r apps/rider-api/app/core apps/<other>/app/core`. A larger organisation would publish it as an internal library instead.
- **`app/proto/`** in pricing-service and rider-api: the gRPC contract (pricing-service's README explains how to regenerate it).
- The tests need no containers: they use fakes and an in-memory Valkey (fakeredis). Some optional tests run against the real thing when you point them at it:
  - `PG_TEST_URL` (rider-api, trip-service, payment-service): migrations and the store SQL against a real Postgres
  - `NATS_TEST_URL` (notification-service): the JetStream consumer against a real `nats-server -js`

  CI can run them with a Postgres service container (topic 13).
