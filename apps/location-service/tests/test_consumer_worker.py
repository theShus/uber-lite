"""The shared Kafka consumer loop (app/core/kafka.py), with a fake confluent-kafka Consumer.

Covers the at-least-once rules every consumer service relies on: an offset is stored only
after success, a failure seeks back and retries, a poison message is skipped.
"""

import threading
import time

import pytest
from confluent_kafka import TopicPartition

from app.core import kafka as core_kafka


class FakeMsg:
    def __init__(self, offset: int, value: bytes, partition: int = 0):
        self._offset, self._value, self._partition = offset, value, partition

    def value(self):
        return self._value

    def error(self):
        return None

    def topic(self):
        return "driver.locations"

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset

    def key(self):
        return b"d1"


class FakeConsumer:
    """Replays a fixed list of messages; seek() puts a message back at the front, like a redelivery."""

    instances: list["FakeConsumer"] = []

    def __init__(self, config):
        self.config = config
        self.log = {
            0: FakeMsg(0, b'{"n": 0}'),
            1: FakeMsg(1, b"this is not json"),
            2: FakeMsg(2, b'{"n": 2}'),
            3: FakeMsg(3, b'{"n": 3}'),
        }
        self.queue = [self.log[o] for o in sorted(self.log)]
        self.stored: list[int] = []
        self.seeks: list[int] = []
        self.closed = False
        self.lock = threading.Lock()
        FakeConsumer.instances.append(self)

    def subscribe(self, topics, on_assign, on_revoke, on_lost):
        on_assign(self, [TopicPartition(topics[0], 0)])

    def poll(self, timeout):
        with self.lock:
            if self.queue:
                return self.queue.pop(0)
        time.sleep(0.01)
        return None

    def seek(self, tp):
        with self.lock:
            self.seeks.append(tp.offset)
            self.queue.insert(0, self.log[tp.offset])

    def store_offsets(self, msg):
        self.stored.append(msg.offset())

    def commit(self, asynchronous=True):
        pass

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_kafka(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP", "kafka:9092")
    monkeypatch.setattr(core_kafka, "Consumer", FakeConsumer)
    FakeConsumer.instances.clear()


def run_until(worker, condition, timeout=5.0):
    worker.start()
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop()


def test_success_poison_and_retry():
    calls: list[int] = []
    failed_once = {"done": False}

    def handler(event, msg):
        calls.append(event["n"])
        if event["n"] == 2 and not failed_once["done"]:
            failed_once["done"] = True
            raise ConnectionError("valkey is down for a moment")

    worker = core_kafka.ConsumerWorker("test", "test-group", "driver.locations", handler)
    run_until(worker, lambda: len(FakeConsumer.instances) and FakeConsumer.instances[0].stored[-1:] == [3])
    consumer = FakeConsumer.instances[0]

    assert calls == [0, 2, 2, 3]  # 1 was poison (never reached the handler), 2 was retried
    assert consumer.seeks == [2]  # the failed message was re-read, not skipped
    assert consumer.stored == [0, 1, 2, 3]  # offsets stored only after each message was done
    assert consumer.closed  # left the group cleanly on stop()
    assert worker.assigned == {0}


def test_classic_protocol_defaults(monkeypatch):
    monkeypatch.delenv("KAFKA_GROUP_PROTOCOL", raising=False)
    worker = core_kafka.ConsumerWorker("test", "g", "driver.locations", lambda e, m: None)
    assert worker._config["partition.assignment.strategy"] == "cooperative-sticky"
    assert worker._config["enable.auto.offset.store"] is False
    assert "group.protocol" not in worker._config


def test_kip_848_protocol(monkeypatch):
    monkeypatch.setenv("KAFKA_GROUP_PROTOCOL", "consumer")
    worker = core_kafka.ConsumerWorker("test", "g", "driver.locations", lambda e, m: None)
    assert worker._config["group.protocol"] == "consumer"
    # with KIP-848 the broker owns assignment: client-side strategies and session timeouts aren't allowed
    assert "partition.assignment.strategy" not in worker._config
    assert "session.timeout.ms" not in worker._config
