# simulator

The load generator: simulated **drivers** drive around Berlin and send their positions to driver-api, and simulated **riders** request rides from rider-api. It's a one-off command (a Job in Kubernetes), not a server.

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
RIDER_API=http://localhost:8081 DRIVER_API=http://localhost:8082 python -m app.simulate --drivers 50 --riders 10 --duration 2m
```
For the Dockerfile (topic 09): `ENTRYPOINT ["python", "-m", "app.simulate"]`, so the arguments come from `CMD`, `docker compose run … <args>`, or the Job's `args`. The 12.6 Job runs it as UID **10001** with a read-only root filesystem; it needs neither a writable disk nor a port.

## Options
| Option | Default | Meaning |
|---|---|---|
| `--drivers N` | 50 | drivers, each pinging every `--ping-interval` seconds (±20%) |
| `--riders N` | 10 | riders, each requesting a ride on average every `--ride-interval` seconds (Poisson arrivals: bursty, like real demand) |
| `--duration D` | 2m | `90s`, `5m`, `1h`, `1m30s` |
| `--rush-hour` | off | **5×** the riders: demand outruns the drivers, surge pricing rises, unmatched rides appear, consumers fall behind |
| `--ping-interval S` | 2.0 | seconds between one driver's pings |
| `--ride-interval S` | 30.0 | mean seconds between one rider's requests |
| `--max-error-rate R` | none | exit **1** if more than this share of ride requests failed: a **smoke test** for a promotion pipeline (topic 18) |
| `--seed N` | random | repeatable runs |

**Load, roughly:**
- pings per second ≈ drivers ÷ ping interval (50 drivers → 25/s, 300 → 150/s)
- rides per second ≈ riders ÷ ride interval (10 riders → 0.33/s; rush hour with 30 riders → 5/s)

For raw HTTP load on one endpoint, use k6 instead (16.3). The simulator exercises the whole system.

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `RIDER_API` | `http://rider-api` | in-cluster: the Service on port 80 |
| `DRIVER_API` | `http://driver-api` | |
| `RIDER_API_KEY` | unset | sent as the `apikey` header, for going through Kong's key-auth (14.3) instead of calling rider-api directly |
| `LOG_LEVEL` | `INFO` | |

## Output
JSON log lines: `simulation starting`, a `progress` line every 30 s, and `simulation finished` with a summary per kind of request:
```json
{"msg": "simulation finished", "rides": {"sent": 41, "ok": 41, "error_rate": 0.0, "statuses": {"202": 41}, "p50_ms": 38.2, "p95_ms": 95.0},
 "pings": {"sent": 3012, "ok": 3012, "error_rate": 0.0, "statuses": {"202": 3012}, "p50_ms": 6.1, "p95_ms": 14.8}}
```
Errors are counted by status code (`503`) or exception name (`ConnectError`). SIGTERM (a Job deleted mid-run) stops it cleanly.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```
The tests run short simulations against fake APIs (`httpx.MockTransport`).
