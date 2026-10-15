"""PgRideStore against a real Postgres: the transactional outbox, rollbacks, SKIP LOCKED.

Skipped unless PG_TEST_URL points at a database rider-api may own, for example
    PG_TEST_URL=postgresql://rides:rides@localhost:5432/rides pytest -q tests/test_store_pg.py
It runs the migrations first, then works with fresh random ride IDs, so it can share a database.
"""

import asyncio
import os
import uuid

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.core import db
from app.migrations import MIGRATIONS
from app.store import PgRideStore

URL = os.environ.get("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set PG_TEST_URL to a real Postgres database")


def new_ride() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "rider_id": "r1",
        "pickup_lat": 52.52,
        "pickup_lon": 13.40,
        "dropoff_lat": 52.50,
        "dropoff_lon": 13.45,
        "amount_cents": 736,
        "currency": "EUR",
        "surge": 1.0,
        "quote_id": "q1",
    }


async def drop(topic, key, value):
    pass


@pytest.fixture
async def store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    db.migrate(MIGRATIONS)
    pool = AsyncConnectionPool(URL, min_size=1, max_size=4, open=False)
    await pool.open()
    s = PgRideStore(pool)
    while await s.relay_outbox(drop):  # start from an empty outbox
        pass
    yield s
    await pool.close()


async def test_the_ride_and_its_event_are_written_together(store):
    ride = new_ride()
    await store.create(ride, "ride.requested", {"ride_id": ride["id"]})
    row = await store.get(ride["id"])
    assert row["status"] == "requested" and row["amount_cents"] == 736

    sent = []

    async def send(topic, key, value):
        sent.append((topic, key, value))

    assert await store.relay_outbox(send) == 1
    assert sent == [("ride.requested", ride["id"], {"ride_id": ride["id"]})]
    assert await store.outbox_size() == 0


async def test_a_duplicate_ride_writes_neither_row(store):
    ride = new_ride()
    await store.create(ride, "ride.requested", {"ride_id": ride["id"]})
    with pytest.raises(psycopg.errors.UniqueViolation):
        await store.create(ride, "ride.requested", {"ride_id": ride["id"]})
    assert await store.outbox_size() == 1  # the second event rolled back with its ride


async def test_a_failed_send_keeps_the_event_for_the_next_pass(store):
    ride = new_ride()
    await store.create(ride, "ride.requested", {"ride_id": ride["id"]})

    async def kafka_down(topic, key, value):
        raise RuntimeError("kafka is down")

    with pytest.raises(RuntimeError):
        await store.relay_outbox(kafka_down)
    assert await store.outbox_size() == 1
    assert await store.relay_outbox(drop) == 1


async def test_two_relays_never_send_the_same_row(store):
    """Replica A holds the row locked while it publishes; replica B skips it instead of waiting or re-sending."""
    ride = new_ride()
    await store.create(ride, "ride.requested", {"ride_id": ride["id"]})
    in_send, release = asyncio.Event(), asyncio.Event()

    async def slow_send(topic, key, value):
        in_send.set()
        await release.wait()

    a = asyncio.create_task(store.relay_outbox(slow_send))
    await in_send.wait()
    assert await store.relay_outbox(drop) == 0  # B: the row is locked by A, SKIP LOCKED skips it
    release.set()
    assert await a == 1
    assert await store.outbox_size() == 0
