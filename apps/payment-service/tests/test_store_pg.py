"""PgPaymentStore and PaymentProcessor against a real Postgres: one payment per ride, whatever is redelivered.

Skipped unless PG_TEST_URL points at a database payment-service may own, for example
    PG_TEST_URL=postgresql://payments:payments@localhost:5432/payments pytest -q tests/test_store_pg.py
"""

import os
import random
import uuid

import pytest
from psycopg_pool import ConnectionPool

from app.core import db
from app.migrations import MIGRATIONS
from app.payments import PaymentProcessor, PgPaymentStore
from app.psp import FakePSP

URL = os.environ.get("PG_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set PG_TEST_URL to a real Postgres database")


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send_sync(self, topic, key, value):
        self.sent.append((topic, key, value))


class CountingPSP(FakePSP):
    def __init__(self):
        super().__init__(latency_ms=0, failure_rate=0, rng=random.Random(1), sleep=lambda s: None)
        self.calls = 0

    def charge(self, *args):
        self.calls += 1
        return super().charge(*args)


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    db.migrate(MIGRATIONS)
    pool = ConnectionPool(URL, min_size=1, max_size=2, open=False)
    pool.open()
    yield PgPaymentStore(pool)
    pool.close()


def test_begin_is_idempotent_and_finish_settles(store):
    ride_id = str(uuid.uuid4())
    assert store.begin(ride_id, 736, "EUR")["status"] == "pending"
    assert store.begin(ride_id, 999, "EUR")["amount_cents"] == 736  # the first insert wins
    assert store.record_attempt(ride_id) == 1
    store.finish(ride_id, "charged", "psp_x")
    row = store.begin(ride_id, 736, "EUR")
    assert row["status"] == "charged" and row["psp_reference"] == "psp_x" and row["attempts"] == 1


def test_a_redelivered_trip_completed_is_never_charged_twice(store):
    producer, psp = FakeProducer(), CountingPSP()
    processor = PaymentProcessor(store, producer, psp, sleep=lambda s: None)
    event = {"ride_id": str(uuid.uuid4()), "price": {"amount_cents": 736, "currency": "EUR"}}
    processor.handle(event, None)
    processor.handle(event, None)  # Kafka redelivers it
    assert psp.calls == 1
    assert [v["status"] for _, _, v in producer.sent] == ["charged", "charged"]  # settled again, not charged again
