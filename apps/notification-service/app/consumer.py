"""The JetStream pull consumer: notify.> from the stream NOTIFY, durable consumer notification-service.

trip-service declares the same stream and consumer with the same settings (its app/notifier.py),
because with KEDA scaling this service to zero, it may not be running to create them. Keep both in sync.
"""

import asyncio
import json
import logging

import nats
import nats.errors
from nats.js.api import AckPolicy, ConsumerConfig, StorageType, StreamConfig
from nats.js.errors import BadRequestError, NotFoundError
from prometheus_client import Counter

STREAM = "NOTIFY"
SUBJECTS = ["notify.>"]
DURABLE = "notification-service"
BATCH = 10

log = logging.getLogger("consumer")

SENT = Counter("notifications_sent_total", "Push notifications 'sent'", ["kind"])
DROPPED = Counter("notifications_dropped_total", "Messages that can't be processed (terminated, not redelivered)")


async def ensure_stream_and_consumer(js) -> None:
    try:
        await js.stream_info(STREAM)
    except NotFoundError:
        await js.add_stream(StreamConfig(name=STREAM, subjects=SUBJECTS, storage=StorageType.FILE, max_age=24 * 3600))
    try:
        await js.add_consumer(
            STREAM,
            ConsumerConfig(
                durable_name=DURABLE,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=30,
                max_deliver=5,
                filter_subject="notify.>",
            ),
        )
    except BadRequestError as exc:
        log.warning("durable consumer differs from ours", extra={"consumer": DURABLE, "error": str(exc)})


def parse_subject(subject: str) -> tuple[str, str]:
    """notify.rider.r1 → ('rider', 'r1')"""
    parts = subject.split(".")
    if len(parts) != 3 or parts[0] != "notify":
        raise ValueError(f"unexpected subject {subject}")
    return parts[1], parts[2]


async def process(msg, delay_s: float = 0.0) -> None:
    """One message: 'send' the push (a log line), then ack. Unparseable messages are terminated."""
    if delay_s:
        await asyncio.sleep(delay_s)
    try:
        kind, target = parse_subject(msg.subject)
        payload = json.loads(msg.data)
        message = str(payload["message"])
    except (ValueError, KeyError, TypeError) as exc:
        DROPPED.inc()
        log.error("dropping a notification that can't be processed", extra={"subject": msg.subject, "error": str(exc)})
        await msg.term()  # tell JetStream never to redeliver it
        return
    log.info(
        f"sending push to {kind} {target}: {message}",
        extra={"kind": kind, "target": target, "ride_id": payload.get("ride_id")},
    )
    SENT.labels(kind).inc()
    await msg.ack()


class NotificationConsumer:
    def __init__(self, url: str, name: str, delay_s: float = 0.0):
        self.url = url
        self.name = name
        self.delay_s = delay_s
        self.nc = None
        self.sub = None
        self._stop = asyncio.Event()

    async def check(self) -> None:
        if self.nc is None or not self.nc.is_connected or self.sub is None:
            raise ConnectionError("not consuming from NATS")

    async def _connect(self) -> None:
        self.nc = await nats.connect(
            self.url, name=self.name, connect_timeout=2, max_reconnect_attempts=-1, reconnect_time_wait=2
        )
        js = self.nc.jetstream()
        await ensure_stream_and_consumer(js)
        self.sub = await js.pull_subscribe_bind(durable=DURABLE, stream=STREAM)
        log.info("consuming", extra={"stream": STREAM, "consumer": DURABLE})

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.sub is None:
                    await self._connect()
                try:
                    messages = await self.sub.fetch(BATCH, timeout=5)
                except nats.errors.TimeoutError:
                    continue  # nothing pending
                for msg in messages:
                    await process(msg, self.delay_s)
            except Exception as exc:
                log.warning("NATS consumer problem, retrying", extra={"error": f"{type(exc).__name__}: {exc}"})
                await self._reset()
                await asyncio.sleep(2)

    async def _reset(self) -> None:
        self.sub = None
        if self.nc is not None:
            try:
                await self.nc.close()
            finally:
                self.nc = None

    async def stop(self) -> None:
        self._stop.set()
        if self.nc is not None:
            await self.nc.drain()
