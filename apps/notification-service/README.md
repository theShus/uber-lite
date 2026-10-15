# notification-service

NATS JetStream consumer: reads `notify.>` from the stream **`NOTIFY`** through the durable consumer **`notification-service`**, and "sends" each push notification (it logs it). It's the service that **scales to zero** with KEDA when nothing is pending (topic 21).

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
NATS_URL=nats://localhost:4222 uvicorn app.main:app --host 0.0.0.0 --port 8080
```
The consumer is a background task in the uvicorn process; HTTP serves only health and metrics.

## What it does
- **Pull consumer:** fetches up to 10 messages at a time (waiting up to 5 s when there are none), processes them one by one, and **acks** each after it's "sent". Log line: `sending push to rider r1: Your driver is on the way`.
- **Unacked messages come back:** a pod that dies mid-batch never acks, and JetStream redelivers after `ack_wait` (30 s), up to `max_deliver` (5) times. Delivery is at-least-once here too.
- **Unparseable messages are terminated** (`term`), so JetStream doesn't redeliver them 5 times for nothing. Counted in `notifications_dropped_total`.
- **Connects in the background:** if NATS isn't up yet, the pod starts, reports unready, and retries every 2 s.

## Scale to zero (topic 21)
Messages wait **in the stream** while no consumer runs, unlike core NATS, where they'd be lost (10.3). KEDA's NATS JetStream scaler reads the durable consumer's **pending count**:
- 0 pending → 0 pods
- messages arrive → a pod starts and drains them → back to 0 after the cooldown

That only works if the durable consumer exists while no pod is running. That's why **trip-service creates it too**, with identical settings (its `app/notifier.py`). Change one, change both.

`PROCESSING_DELAY_MS` slows each notification down, so a backlog builds and KEDA scales **out**, not just from zero.

## Contracts
| | |
|---|---|
| Readiness | connected to NATS **and** bound to the durable consumer |
| Logs | `sending push to <rider\|driver> <id>: <message>` with `ride_id`; errors for dropped messages |
| Shutdown | stop fetching; `drain` acks what's in flight, then closes |

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `NATS_URL` | (required) | e.g. `nats://nats:4222` |
| `PROCESSING_DELAY_MS` | `0` | artificial slowness per notification |
| `LOG_LEVEL`, `SHUTDOWN_DELAY_SECONDS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | | as everywhere |

**Messages** (subject `notify.rider.<id>` or `notify.driver.<id>`):
```json
{"ride_id": "…", "message": "Your driver is on the way"}
```

## Metrics
`notifications_sent_total{kind}`, `notifications_dropped_total`, and the HTTP metrics. The pending count is read from NATS, not from the app.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
NATS_TEST_URL=nats://localhost:4222 pytest -q        # also runs one test against a real nats-server -js
```
