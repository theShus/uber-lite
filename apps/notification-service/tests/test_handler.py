"""The message handler, the probes and, when KAFKA_TEST_BOOTSTRAP points at a broker, the real thing."""

import json
import os
import time
import uuid

import httpx
import pytest

from app.core.kafka import PoisonMessage
from app.main import SENT, create_app, handle


def event(kind="rider", target="r1", message="Your driver is on the way"):
    return {"kind": kind, "target": target, "ride_id": "x", "message": message}


def test_a_notification_is_sent(caplog):
    before = SENT.labels("rider")._value.get()
    with caplog.at_level("INFO"):
        handle(event(), None)
    assert "sending push to rider r1: Your driver is on the way" in caplog.text
    assert SENT.labels("rider")._value.get() == before + 1


def test_drivers_get_notifications_too(caplog):
    with caplog.at_level("INFO"):
        handle(event(kind="driver", target="d7", message="New ride: head to the pickup"), None)
    assert "sending push to driver d7: New ride: head to the pickup" in caplog.text


@pytest.mark.parametrize(
    "bad",
    [
        {"target": "r1", "message": "hi"},  # no kind
        {"kind": "rider", "message": "hi"},  # no target
        {"kind": "rider", "target": "r1"},  # no message
        event(kind="passenger"),  # nobody we can send to
    ],
)
def test_bad_events_are_poison(bad):
    with pytest.raises(PoisonMessage):
        handle(bad, None)


async def test_probes_without_a_consumer():
    app = create_app(start_consumer=False)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://n") as http:
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.get("/readyz")).status_code == 200


@pytest.mark.skipif(not os.environ.get("KAFKA_TEST_BOOTSTRAP"), reason="set KAFKA_TEST_BOOTSTRAP to a Kafka broker")
def test_against_a_real_kafka(monkeypatch):
    """A throwaway topic and group: produce one notification, see the shared consumer loop hand it to handle()."""
    from confluent_kafka import Producer
    from confluent_kafka.admin import AdminClient, NewTopic

    from app.core.kafka import ConsumerWorker

    bootstrap = os.environ["KAFKA_TEST_BOOTSTRAP"]
    monkeypatch.setenv("KAFKA_BOOTSTRAP", bootstrap)
    suffix = uuid.uuid4().hex[:8]
    topic = f"notification.requested.test-{suffix}"
    admin = AdminClient({"bootstrap.servers": bootstrap})
    admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])[topic].result(10)
    received = []

    def capture(evt, msg):
        handle(evt, msg)
        received.append(evt)

    worker = ConsumerWorker("test", f"test-{suffix}", topic, capture)
    worker.start()
    try:
        producer = Producer({"bootstrap.servers": bootstrap})
        producer.produce(topic, key=b"rider:it", value=json.dumps(event(target="it", message="hello")).encode())
        assert producer.flush(10) == 0
        deadline = time.monotonic() + 30
        while not received and time.monotonic() < deadline:
            time.sleep(0.2)
        assert received == [event(target="it", message="hello")]
    finally:
        worker.stop()
        admin.delete_topics([topic])
