# notification-service

> **Changed 2026-10-25:** this service used to read a NATS JetStream stream (`NOTIFY`, subjects `notify.>`). It's now a Kafka consumer like the others. Why: [docs/app-reference.md, History](../../docs/app-reference.md#history).

Kafka consumer: reads **`notification.requested`** in consumer group **`notification-service`**, and "sends" each push notification (it logs it). It's the service that **scales to zero** with KEDA when the group has no lag (topic 21).

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
KAFKA_BOOTSTRAP=localhost:9092 uvicorn app.main:app --host 0.0.0.0 --port 8080
```
The consumer runs in its own thread in the uvicorn process; HTTP serves only health and metrics.

## What it does
- **The shared consumer loop** (`app/core/kafka.py`, the same as every other consumer): one message at a time, and the offset is stored only after the push is "sent", so delivery is **at-least-once**. Log line: `sending push to rider r1: Your driver is on the way`.
- **A crash or a deploy mid-message:** the offset wasn't stored, so the next consumer in the group gets the message again. The rider may see the same push twice; a real push provider's idempotency key would prevent that.
- **A malformed event** (not JSON, a missing field, an unknown `kind`) is a **poison message**: logged, counted as `kafka_messages_consumed_total{result="dropped"}`, and skipped, so it can't hold up the notifications behind it.
- **Any other error** retries the same message with backoff. On Kafka that means the messages behind it on the same partition wait: the price of a log, accepted here because a notification has no slow dependency to fail.

## Scale to zero (topic 21)
Messages wait **in the topic** while no pod runs, and the group's committed offsets live in Kafka, not in a pod. So KEDA's Kafka scaler can compute the lag with zero replicas:
- no lag → 0 pods
- messages arrive → lag > 0 → a pod starts and catches up → back to 0 after the cooldown

**A partition the group has never committed to** (a fresh group, or one that never received a message) has no offset at all. The consumer then starts from the beginning (`auto.offset.reset=earliest`), so the ScaledObject must say **`offsetResetPolicy: earliest`** too: KEDA then counts what's retained in that partition as lag. With `latest` (and the default `scaleToZeroOnInvalidOffset: false`), KEDA reports a lag of 1 for every such partition, and the service never reaches zero.

`PROCESSING_DELAY_MS` slows each notification down, so a backlog builds and KEDA scales **out**, not just from zero. Replicas are capped at the topic's partition count (6).

## Contracts
| | |
|---|---|
| Readiness | Kafka reachable, `notification.requested` exists, consumer thread alive |
| Logs | `sending push to <rider\|driver> <id>: <message>` with `kind`, `target`, `ride_id`; errors for dropped messages; partition assignments and revocations |
| Shutdown | finishes the message in hand, commits, leaves the group |

## Environment
| Variable | Default | Meaning |
|---|---|---|
| `KAFKA_BOOTSTRAP` | (required) | e.g. `kafka:9092` |
| `PROCESSING_DELAY_MS` | `0` | artificial slowness per notification |
| consumer knobs | | see location-service's README (`KAFKA_GROUP_PROTOCOL`, …) |
| `LOG_LEVEL`, `SHUTDOWN_DELAY_SECONDS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | | as everywhere |

**Event `notification.requested`** (key = `<kind>:<id>`, so one recipient's notifications stay in order on one partition):
```json
{"kind": "rider", "target": "r1", "ride_id": "…", "message": "Your driver is on the way"}
```

## Metrics
`notifications_sent_total{kind}`, the Kafka consumer metrics (`kafka_messages_consumed_total{topic,result}`, `kafka_message_processing_seconds`), and the HTTP metrics. The lag comes from Kafka (Kafka Exporter, KEDA), not from the app.

## Tests
```bash
pip install -r requirements-dev.txt && pytest -q
KAFKA_TEST_BOOTSTRAP=localhost:9092 pytest -q        # also runs one test against a real broker
```
The real-broker test creates a throwaway topic and group, produces one notification and checks that the shared consumer loop hands it to the handler. Verified against Kafka 4.1.0.
