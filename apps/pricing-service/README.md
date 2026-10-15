# pricing-service

Internal **gRPC** service: `Quote(pickup, dropoff)` → a fare in EUR cents, with **surge** from live demand and supply in Valkey. Only rider-api calls it (its NetworkPolicy says so, topic 11).

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
VALKEY_URL=redis://localhost:6379/0 uvicorn app.main:app --host 0.0.0.0 --port 8080
```
One process serves **two ports**:

| Port | Protocol | What |
|---|---|---|
| **50051** | gRPC (HTTP/2) | `uberlite.pricing.v1.Pricing/Quote`, `grpc.health.v1.Health`, server reflection |
| **8080** | HTTP | `/healthz`, `/readyz`, `/metrics` |

For the Dockerfile (topic 09): `EXPOSE 8080 50051`, and the same exec-form `uvicorn` entrypoint as the other services. The gRPC server starts and stops with the HTTP app.

## Call it with grpcurl
Reflection is on, so no `.proto` file is needed:
```bash
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext localhost:50051 describe uberlite.pricing.v1.Pricing
grpcurl -plaintext -d '{"rider_id":"r1","pickup":{"lat":52.5219,"lon":13.4132},"dropoff":{"lat":52.4991,"lon":13.4180}}' \
  localhost:50051 uberlite.pricing.v1.Pricing/Quote
grpcurl -plaintext -d '{"service":"uberlite.pricing.v1.Pricing"}' localhost:50051 grpc.health.v1.Health/Check
```
A reply (JSON names are camelCase):
```json
{"quoteId": "…", "amountCents": "557", "currency": "EUR", "surge": 1, "distanceKm": 2.561}
```
`amountCents` is a string because it's an `int64`: JSON numbers can't hold every int64 exactly.

## How a price is made
- **distance:** great-circle (haversine) from pickup to dropoff
- **demand:** `INCR demand:<cell>` on every quote, in a 5-minute window (`EXPIRE … NX`). A cell is a 0.05° × 0.05° grid square, e.g. `cell:1050:268` in Berlin Mitte.
- **supply:** drivers within 3 km of the pickup, from `GEOSEARCH drivers:geo` (location-service keeps that index)
- **surge:** 1.0 while demand ≤ supply, then +0.25 per extra request per driver, capped at **3.0**, one decimal
- **fare:** `(250 + 120 × km) × surge` cents, at least 500

## Contracts
| | |
|---|---|
| Liveness | `/healthz` is always 200 while the process runs |
| Readiness | `/readyz` is 200 only if Valkey answers `PING` (and not shutting down). Every pricing pod depends on Valkey, so a Valkey outage makes **all** of them unready at once (11.8 §5) |
| Logs | JSON; one line per Quote: `"msg": "quote"` with `rider_id`, `cell`, `demand`, `supply`, `surge`, `amount_cents` |
| Shutdown | SIGTERM → readiness 503, gRPC health `NOT_SERVING`, in-flight calls get 10 s, then exit |
| Connections | a **max connection age of 5 minutes**: clients reconnect, re-resolve DNS, and find new pods (16.3) |
| Errors | `INVALID_ARGUMENT` for bad coordinates; `UNAVAILABLE` when Valkey can't be reached |

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `VALKEY_URL` | (required) | e.g. `redis://valkey:6379/0` |
| `GRPC_PORT` | `50051` | |
| `LOG_LEVEL` | `INFO` | |
| `SHUTDOWN_DELAY_SECONDS` | `0` | keep serving this long after SIGTERM (an alternative to a preStop sleep) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | tracing on when set (topic 19), e.g. `http://alloy:4318` |

## Metrics (`/metrics`)
- `grpc_server_handled_total{method,code}`, `grpc_server_handling_seconds{method}`
- `pricing_surge_multiplier` (histogram of the surge per quote)
- the HTTP metrics every service has (`http_requests_total`, `http_request_duration_seconds`)

## Changing the contract
`app/proto/pricing.proto` is the source; `pricing_pb2*.py` are generated and checked in, so image builds need no compiler. rider-api has an identical copy (it's the client). After editing the `.proto`, in **both** services:
```bash
pip install -r requirements-dev.txt
python -m grpc_tools.protoc -I . --python_out=. --pyi_out=. --grpc_python_out=. app/proto/pricing.proto
```
Add fields with new numbers; never reuse or renumber a field. Old clients ignore fields they don't know, which is what makes rolling updates safe.

## Tests
```bash
pip install -r requirements-dev.txt
pytest -q
```
The tests run the real gRPC server against an in-memory Valkey (fakeredis), so they need no containers.
