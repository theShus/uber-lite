"""Kafka with confluent-kafka (librdkafka): a producer, an at-least-once consumer, a readiness check.

Configuration comes from the environment, so labs can change behaviour without code:
  KAFKA_BOOTSTRAP              brokers, e.g. kafka:9092 (required)
  KAFKA_ACKS                   all (default) | 1 | 0          producer durability (topic 20)
  KAFKA_LINGER_MS              5                               wait to batch more records
  KAFKA_COMPRESSION            lz4                             none | gzip | snappy | lz4 | zstd
  KAFKA_GROUP_PROTOCOL         classic (default) | consumer    "consumer" = KIP-848 rebalancing
  KAFKA_ASSIGNMENT_STRATEGY    cooperative-sticky              classic protocol only
  KAFKA_SESSION_TIMEOUT_MS     45000                           classic protocol only
  KAFKA_MAX_POLL_INTERVAL_MS   300000
  PROCESSING_DELAY_MS          0                               slow every message down: makes lag on demand
"""

import asyncio
import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer, TopicPartition
from confluent_kafka.admin import AdminClient

from .config import env_int, env_str
from .metrics import KAFKA_CONSUMED, KAFKA_PROCESSING, KAFKA_PRODUCE_ERRORS, KAFKA_PRODUCED

log = logging.getLogger("kafka")


def base_config(service: str) -> dict:
    return {"bootstrap.servers": env_str("KAFKA_BOOTSTRAP"), "client.id": f"{service}-{socket.gethostname()}"}


class PoisonMessage(Exception):
    """A message that can never be processed (not JSON, missing fields). It's logged and skipped."""


def decode(msg: Message) -> dict:
    try:
        value = json.loads(msg.value())
    except (TypeError, ValueError) as exc:
        raise PoisonMessage(f"not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PoisonMessage("not a JSON object")
    return value


def readiness_check(service: str, topics: list[str]) -> Callable[[], None]:
    """A blocking check: brokers answer, and every topic we need exists (auto-creation is off)."""
    admin = AdminClient(base_config(service))

    def check() -> None:
        metadata = admin.list_topics(timeout=2)
        if not metadata.brokers:
            raise RuntimeError("no brokers")
        missing = [t for t in topics if t not in metadata.topics]
        if missing:
            raise RuntimeError(f"missing topics: {', '.join(missing)}")

    return check


class KafkaProducer:
    """Thread-safe. produce() queues and returns at once; send() waits for the broker's acknowledgement."""

    def __init__(self, service: str):
        acks = os.environ.get("KAFKA_ACKS", "all")
        self._producer = Producer(
            base_config(service)
            | {
                "acks": acks,
                # idempotence (no duplicates or reordering from retries) requires acks=all
                "enable.idempotence": acks == "all",
                "linger.ms": env_int("KAFKA_LINGER_MS", 5),
                "compression.type": os.environ.get("KAFKA_COMPRESSION", "lz4"),
            }
        )
        self._stop = threading.Event()
        self._poller = threading.Thread(target=self._poll_loop, name="kafka-producer-poll", daemon=True)
        self._poller.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._producer.poll(0.1)  # runs delivery callbacks

    def produce(self, topic: str, key: str, value: dict, on_delivery: Callable | None = None) -> None:
        """Queue one message. Raises BufferError when the local queue is full (backpressure)."""

        def report(err, msg):
            if err is not None:
                KAFKA_PRODUCE_ERRORS.labels(topic).inc()
                log.warning("produce failed", extra={"topic": topic, "key": key, "error": str(err)})
            else:
                KAFKA_PRODUCED.labels(topic).inc()
            if on_delivery:
                on_delivery(err, msg)

        self._producer.produce(topic, key=key.encode(), value=json.dumps(value).encode(), on_delivery=report)

    async def send(self, topic: str, key: str, value: dict, timeout: float = 10.0) -> None:
        """Produce, and wait (asynchronously) until the broker acknowledged the message."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def done(err, _msg):
            loop.call_soon_threadsafe(_settle, future, err)

        self.produce(topic, key, value, on_delivery=done)
        await asyncio.wait_for(future, timeout)

    def send_sync(self, topic: str, key: str, value: dict, timeout: float = 10.0) -> None:
        """Produce, and block until acknowledged. For consumer threads."""
        acked = threading.Event()
        errors = []

        def done(err, _msg):
            if err is not None:
                errors.append(err)
            acked.set()

        self.produce(topic, key, value, on_delivery=done)
        if not acked.wait(timeout):
            raise TimeoutError(f"no acknowledgement for {topic} within {timeout}s")
        if errors:
            raise KafkaException(errors[0])

    def close(self, timeout: float = 10.0) -> None:
        remaining = self._producer.flush(timeout)
        self._stop.set()
        if remaining:
            log.warning("messages not delivered at shutdown", extra={"count": remaining})


def _settle(future: asyncio.Future, err) -> None:
    if future.done():
        return
    if err is not None:
        future.set_exception(KafkaException(err))
    else:
        future.set_result(None)


Handler = Callable[[dict, Message], None]


class ConsumerWorker:
    """One consumer in a group, polling in its own thread. At-least-once:

    - an offset is stored only after the handler succeeded, and auto-commit commits stored offsets
    - a handler error: seek back to the same message and retry with backoff (polling continues,
      so the consumer stays in the group). The partition stalls, lag grows, nothing is lost.
    - a PoisonMessage: logged, counted as dropped, skipped
    """

    def __init__(self, service: str, group: str, topic: str, handler: Handler):
        config = base_config(service) | {
            "group.id": group,
            "enable.auto.commit": True,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "max.poll.interval.ms": env_int("KAFKA_MAX_POLL_INTERVAL_MS", 300000),
        }
        if os.environ.get("KAFKA_GROUP_PROTOCOL", "classic") == "consumer":
            config["group.protocol"] = "consumer"  # KIP-848: the broker assigns partitions
        else:
            config["partition.assignment.strategy"] = os.environ.get("KAFKA_ASSIGNMENT_STRATEGY", "cooperative-sticky")
            config["session.timeout.ms"] = env_int("KAFKA_SESSION_TIMEOUT_MS", 45000)
        self.topic = topic
        self.handler = handler
        self.delay = env_int("PROCESSING_DELAY_MS", 0) / 1000
        self.assigned: set[int] = set()
        self._config = config
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"consumer-{topic}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def alive_check(self) -> None:
        if not self._thread.is_alive():
            raise RuntimeError("consumer thread is not running")

    # rebalance callbacks: logged, because watching them is half of topic 20
    def _on_assign(self, consumer, partitions):
        self.assigned |= {p.partition for p in partitions}
        log.info(
            "partitions assigned",
            extra={"topic": self.topic, "added": sorted(p.partition for p in partitions), "now": sorted(self.assigned)},
        )

    def _on_revoke(self, consumer, partitions):
        try:
            consumer.commit(asynchronous=False)  # hand over cleanly: commit what we've processed
        except KafkaException as exc:
            if exc.args[0].code() != KafkaError._NO_OFFSET:
                log.warning("commit on revoke failed", extra={"error": str(exc)})
        self.assigned -= {p.partition for p in partitions}
        log.info(
            "partitions revoked",
            extra={
                "topic": self.topic,
                "removed": sorted(p.partition for p in partitions),
                "now": sorted(self.assigned),
            },
        )

    def _on_lost(self, consumer, partitions):
        self.assigned -= {p.partition for p in partitions}
        log.warning("partitions lost", extra={"topic": self.topic, "lost": sorted(p.partition for p in partitions)})

    def _run(self) -> None:
        consumer = Consumer(self._config)
        consumer.subscribe([self.topic], on_assign=self._on_assign, on_revoke=self._on_revoke, on_lost=self._on_lost)
        backoff = 0.5
        try:
            while not self._stop.is_set():
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.warning("consumer error", extra={"error": str(msg.error())})
                    continue
                if self.delay:
                    time.sleep(self.delay)
                started = time.perf_counter()
                where = {"topic": msg.topic(), "partition": msg.partition(), "offset": msg.offset()}
                try:
                    self.handler(decode(msg), msg)
                    KAFKA_CONSUMED.labels(self.topic, "ok").inc()
                except PoisonMessage as exc:
                    KAFKA_CONSUMED.labels(self.topic, "dropped").inc()
                    log.error("dropping a message that can't be processed", extra=where | {"error": str(exc)})
                except Exception as exc:
                    KAFKA_CONSUMED.labels(self.topic, "retry").inc()
                    log.warning("processing failed, retrying", extra=where | {"error": str(exc), "backoff_s": backoff})
                    try:
                        consumer.seek(TopicPartition(msg.topic(), msg.partition(), msg.offset()))
                    except KafkaException as seek_error:  # e.g. the partition was revoked meanwhile
                        log.warning("seek failed", extra=where | {"error": str(seek_error)})
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 5.0)
                    continue
                KAFKA_PROCESSING.labels(self.topic).observe(time.perf_counter() - started)
                backoff = 0.5
                consumer.store_offsets(msg)
        finally:
            consumer.close()  # commits stored offsets and leaves the group: the others rebalance at once
            log.info("consumer closed", extra={"topic": self.topic})
