"""PgTripStore against a real Postgres: idempotent starts and the SKIP LOCKED completion scheduler.

Skipped unless PG_TEST_URL points at a database trip-service may own, for example
    PG_TEST_URL=postgresql://trips:trips@localhost:5432/trips pytest -q tests/test_store_pg.py
"""

import asyncio
import os
import uuid

import pytest
from psycopg_pool import AsyncConnectionPool

from app.core import db
from app.migrations import MIGRATIONS
from app.store import PgTripStore

URL = os.environ.get("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set PG_TEST_URL to a real Postgres database")


def new_trip() -> dict:
    return {"ride_id": str(uuid.uuid4()), "rider_id": "r1", "driver_id": "d1", "amount_cents": 736, "currency": "EUR"}


@pytest.fixture
async def store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    db.migrate(MIGRATIONS)
    pool = AsyncConnectionPool(URL, min_size=1, max_size=4, open=False)
    await pool.open()
    yield PgTripStore(pool)
    await pool.close()


async def test_a_duplicate_ride_matched_starts_no_second_trip(store):
    trip = new_trip()
    assert await store.start(trip, duration_s=60) is True
    assert await store.start(trip, duration_s=60) is False


async def test_due_trips_are_published_then_completed(store):
    trip = new_trip()
    await store.start(trip, duration_s=0)
    published = []

    async def publish(due):
        published.extend(due)

    done = await store.complete_due(publish)
    assert trip["ride_id"] in [t["ride_id"] for t in done]
    assert trip["ride_id"] in [t["ride_id"] for t in published]
    assert (await store.get(trip["ride_id"]))["status"] == "completed"
    assert trip["ride_id"] not in [t["ride_id"] for t in await store.complete_due(publish)]


async def test_a_failed_publish_leaves_the_trip_started(store):
    trip = new_trip()
    await store.start(trip, duration_s=0)

    async def kafka_down(due):
        raise RuntimeError("kafka is down")

    with pytest.raises(RuntimeError):
        await store.complete_due(kafka_down)
    assert (await store.get(trip["ride_id"]))["status"] == "started"


async def test_two_schedulers_split_the_due_trips(store):
    """Replica A has its batch locked while publishing; replica B gets the rest, never the same trips."""

    async def drain(due):
        pass

    while await store.complete_due(drain):  # nothing due from earlier tests
        pass
    trips = [new_trip() for _ in range(4)]
    for t in trips:
        await store.start(t, duration_s=0)
    in_publish, release = asyncio.Event(), asyncio.Event()

    async def slow_publish(due):
        in_publish.set()
        await release.wait()

    a = asyncio.create_task(store.complete_due(slow_publish, batch=2))
    await in_publish.wait()
    b = await store.complete_due(drain, batch=10)  # B skips A's two locked rows
    release.set()
    a_ids, b_ids = {t["ride_id"] for t in await a}, {t["ride_id"] for t in b}
    assert len(a_ids) == 2 and len(b_ids) == 2 and not a_ids & b_ids
    assert a_ids | b_ids == {t["ride_id"] for t in trips}
