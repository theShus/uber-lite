"""Trips in Postgres."""

from collections.abc import Awaitable, Callable

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

Publish = Callable[[list[dict]], Awaitable[None]]


class PgTripStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def check(self) -> None:
        async with self.pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")

    async def start(self, trip: dict, duration_s: float) -> bool:
        """Insert a started trip. False if it already exists (a duplicate ride.matched)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO trips (ride_id, rider_id, driver_id, amount_cents, currency, status, completes_at)
                VALUES (%(ride_id)s, %(rider_id)s, %(driver_id)s, %(amount_cents)s, %(currency)s, 'started',
                        now() + make_interval(secs => %(duration_s)s))
                ON CONFLICT (ride_id) DO NOTHING
                RETURNING ride_id
                """,
                trip | {"duration_s": duration_s},
            )
            return await cur.fetchone() is not None

    async def complete_due(self, publish: Publish, batch: int = 50) -> list[dict]:
        """Complete up to `batch` trips whose drive has ended, publishing their events first.

        One transaction: lock the due rows (SKIP LOCKED, so every replica can run this loop),
        publish trip.completed for them, mark them completed. If publishing fails, nothing
        is marked, and the next tick retries: at-least-once, payment-service is idempotent.
        """
        async with self.pool.connection() as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                """
                SELECT ride_id::text AS ride_id, rider_id, driver_id, amount_cents, currency
                FROM trips WHERE status = 'started' AND completes_at <= now()
                ORDER BY completes_at LIMIT %s FOR UPDATE SKIP LOCKED
                """,
                (batch,),
            )
            due = await cur.fetchall()
            if not due:
                return []
            await publish(due)
            await conn.execute(
                "UPDATE trips SET status = 'completed', completed_at = now() WHERE ride_id = ANY(%s::uuid[])",
                ([t["ride_id"] for t in due],),
            )
            return due

    async def get(self, ride_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT * FROM trips WHERE ride_id = %s", (ride_id,))
            return await cur.fetchone()

    async def close(self) -> None:
        await self.pool.close()
