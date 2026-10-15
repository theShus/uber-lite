"""Where rides live: Postgres, with a transactional outbox for the ride.requested events."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from prometheus_client import Gauge
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger("store")

OUTBOX_PENDING = Gauge("outbox_pending_events", "Events written to the outbox but not yet published")

Send = Callable[[str, str, dict], Awaitable[None]]


class PgRideStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def check(self) -> None:
        async with self.pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")

    async def create(self, ride: dict, topic: str, event: dict) -> None:
        """The ride and its event, in one transaction."""
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO rides (id, rider_id, pickup_lat, pickup_lon, dropoff_lat, dropoff_lon,
                                   amount_cents, currency, surge, quote_id)
                VALUES (%(id)s, %(rider_id)s, %(pickup_lat)s, %(pickup_lon)s, %(dropoff_lat)s, %(dropoff_lon)s,
                        %(amount_cents)s, %(currency)s, %(surge)s, %(quote_id)s)
                """,
                ride,
            )
            await conn.execute(
                "INSERT INTO outbox (topic, key, payload) VALUES (%s, %s, %s)", (topic, ride["id"], Jsonb(event))
            )

    async def get(self, ride_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT * FROM rides WHERE id = %s", (ride_id,))
            return await cur.fetchone()

    async def relay_outbox(self, send: Send, batch: int = 100) -> int:
        """Publish up to `batch` outbox events, then delete them, all in one transaction.

        FOR UPDATE SKIP LOCKED lets every rider-api replica relay at the same time without
        sending the same row twice. If Kafka fails, the transaction rolls back and the rows
        are retried: at-least-once, so consumers must be idempotent (they are).
        """
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT id, topic, key, payload FROM outbox ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED", (batch,)
            )
            rows = await cur.fetchall()
            if not rows:
                return 0
            await asyncio.gather(*(send(topic, key, payload) for _, topic, key, payload in rows))
            await conn.execute("DELETE FROM outbox WHERE id = ANY(%s)", ([row[0] for row in rows],))
            return len(rows)

    async def outbox_size(self) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) FROM outbox")
            return (await cur.fetchone())[0]

    async def close(self) -> None:
        await self.pool.close()


class OutboxRelay:
    """Publishes outbox events soon after each ride is stored (wake()) and every `interval` seconds anyway."""

    def __init__(self, store, send: Send, interval: float = 1.0):
        self.store = store
        self.send = send
        self.interval = interval
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._last_gauge = 0.0

    def wake(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                while await self.store.relay_outbox(self.send):  # drain, batch after batch
                    pass
                await self._update_gauge()
            except Exception as exc:
                log.warning("outbox relay failed, will retry", extra={"error": f"{type(exc).__name__}: {exc}"})
            self._wake.clear()
            with contextlib.suppress(TimeoutError):  # no wake-up: run the periodic pass anyway
                await asyncio.wait_for(self._wake.wait(), self.interval)

    async def _update_gauge(self) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._last_gauge >= 5:
            OUTBOX_PENDING.set(await self.store.outbox_size())
            self._last_gauge = now

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
