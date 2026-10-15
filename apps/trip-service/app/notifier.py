"""Rider and driver notifications over NATS JetStream.

trip-service publishes to notify.rider.<id> and notify.driver.<id>. The stream NOTIFY
(subjects notify.>) stores them until notification-service has acknowledged them.

trip-service also creates the durable consumer notification-service reads from. With
KEDA scaling notification-service to zero (topic 21), nothing else would create it,
and KEDA needs it to exist to see the pending count. notification-service declares the
same consumer with the same settings: keep the two in sync.
"""

import asyncio
import json
import logging

import nats
from nats.js.api import AckPolicy, ConsumerConfig, StorageType, StreamConfig
from nats.js.errors import BadRequestError, NotFoundError

STREAM = "NOTIFY"
SUBJECTS = ["notify.>"]
DURABLE = "notification-service"

log = logging.getLogger("notifier")


async def ensure_stream_and_consumer(js) -> None:
    try:
        await js.stream_info(STREAM)
    except NotFoundError:
        await js.add_stream(StreamConfig(name=STREAM, subjects=SUBJECTS, storage=StorageType.FILE, max_age=24 * 3600))
        log.info("created JetStream stream", extra={"stream": STREAM})
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
    except BadRequestError as exc:  # it exists with different settings: leave it, but say so
        log.warning("durable consumer differs from ours", extra={"consumer": DURABLE, "error": str(exc)})


class NatsNotifier:
    """Connects in the background, so the service starts even when NATS isn't up yet (readiness says so)."""

    def __init__(self, url: str, name: str):
        self.url = url
        self.name = name
        self.nc = None
        self.js = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._connect())

    async def _connect(self) -> None:
        while True:
            try:
                self.nc = await nats.connect(
                    self.url, name=self.name, connect_timeout=2, max_reconnect_attempts=-1, reconnect_time_wait=2
                )
                self.js = self.nc.jetstream()
                await ensure_stream_and_consumer(self.js)
                log.info("connected to NATS", extra={"url": self.url})
                return
            except Exception as exc:
                log.warning("NATS not reachable yet, retrying", extra={"error": f"{type(exc).__name__}: {exc}"})
                if self.nc is not None:
                    await self.nc.close()
                    self.nc = None
                await asyncio.sleep(2)

    async def check(self) -> None:
        if self.nc is None or not self.nc.is_connected:
            raise ConnectionError("not connected to NATS")

    async def publish(self, subject: str, payload: dict) -> None:
        await self.check()
        await self.js.publish(subject, json.dumps(payload).encode(), timeout=5)  # waits for the stream's ack

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self.nc is not None:
            await self.nc.drain()
