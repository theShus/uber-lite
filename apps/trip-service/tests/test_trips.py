"""Trip logic and the HTTP API, with in-memory fakes for Postgres, Kafka and NATS."""

import asyncio
import time
import uuid

import httpx
import pytest

from app.core.kafka import PoisonMessage
from app.main import create_app
from app.trips import TripService


def matched(ride_id=None, rider="r1", driver="d7", cents=736):
    return {
        "ride_id": ride_id or str(uuid.uuid4()),
        "rider_id": rider,
        "driver_id": driver,
        "price": {"amount_cents": cents, "currency": "EUR"},
    }


class FakeStore:
    def __init__(self):
        self.trips: dict[str, dict] = {}
        self.down = False

    async def check(self):
        if self.down:
            raise ConnectionError("postgres is down")

    async def start(self, trip, duration_s):
        await self.check()
        if trip["ride_id"] in self.trips:
            return False
        self.trips[trip["ride_id"]] = trip | {"status": "started", "completes_at": time.monotonic() + duration_s}
        return True

    async def complete_due(self, publish, batch=50):
        now = time.monotonic()
        due = [t for t in self.trips.values() if t["status"] == "started" and t["completes_at"] <= now][:batch]
        if not due:
            return []
        await publish(due)  # if this raises, nothing is marked: like the rolled-back transaction
        for t in due:
            t["status"] = "completed"
        return due

    async def get(self, ride_id):
        return self.trips.get(ride_id)

    async def close(self):
        pass


class FakeProducer:
    def __init__(self):
        self.sent = []
        self.down = False

    async def send(self, topic, key, value):
        if self.down:
            raise RuntimeError("kafka is down")
        self.sent.append((topic, key, value))

    def close(self):
        pass


class FakeNotifier:
    def __init__(self):
        self.published = []
        self.down = False

    async def check(self):
        if self.down:
            raise ConnectionError("not connected to NATS")

    async def publish(self, subject, payload):
        await self.check()
        self.published.append((subject, payload))

    async def close(self):
        pass


@pytest.fixture
def fakes():
    return FakeStore(), FakeProducer(), FakeNotifier()


@pytest.fixture
def trips(fakes):
    store, producer, notifier = fakes
    return TripService(store, producer, notifier, duration_s=0, poll_s=0.01)


async def test_a_match_starts_a_trip_and_notifies_both(trips, fakes):
    store, _, notifier = fakes
    event = matched()
    await trips.on_ride_matched(event)
    assert store.trips[event["ride_id"]]["status"] == "started"
    subjects = [s for s, _ in notifier.published]
    assert subjects == ["notify.rider.r1", "notify.driver.d7"]


async def test_a_duplicate_match_is_ignored(trips, fakes):
    _, _, notifier = fakes
    event = matched()
    await trips.on_ride_matched(event)
    await trips.on_ride_matched(event)
    assert len(notifier.published) == 2  # notified once, not twice


@pytest.mark.parametrize("bad", [{"ride_id": "not-a-uuid"}, {**matched(), "price": None}, {"rider_id": "r1"}])
async def test_bad_events_are_poison(trips, bad):
    with pytest.raises(PoisonMessage):
        await trips.on_ride_matched(bad)


async def test_database_errors_raise_for_a_retry(trips, fakes):
    store, _, _ = fakes
    store.down = True
    with pytest.raises(ConnectionError):
        await trips.on_ride_matched(matched())


async def test_nats_down_doesnt_stop_trips(trips, fakes):
    store, _, notifier = fakes
    notifier.down = True
    event = matched()
    await trips.on_ride_matched(event)  # no exception: notifications are best effort
    assert event["ride_id"] in store.trips


async def test_due_trips_complete_and_publish(trips, fakes):
    store, producer, notifier = fakes
    event = matched(cents=736)
    await trips.on_ride_matched(event)
    assert await trips.complete_batch() == 1
    topic, key, completed = producer.sent[0]
    assert (topic, key) == ("trip.completed", event["ride_id"])
    assert completed["price"] == {"amount_cents": 736, "currency": "EUR"}
    assert store.trips[event["ride_id"]]["status"] == "completed"
    assert notifier.published[-1] == (
        "notify.rider.r1",
        {"ride_id": event["ride_id"], "message": "Trip completed: 7.36 EUR"},
    )


async def test_kafka_down_keeps_the_trip_started_until_it_works(trips, fakes):
    store, producer, _ = fakes
    event = matched()
    await trips.on_ride_matched(event)
    producer.down = True
    with pytest.raises(RuntimeError):
        await trips.complete_batch()
    assert store.trips[event["ride_id"]]["status"] == "started"
    producer.down = False
    assert await trips.complete_batch() == 1


async def test_trips_that_arent_due_wait(fakes):
    store, producer, notifier = fakes
    slow = TripService(store, producer, notifier, duration_s=60)
    await slow.on_ride_matched(matched())
    assert await slow.complete_batch() == 0


@pytest.fixture
async def http(fakes, monkeypatch):
    monkeypatch.setenv("TRIP_DURATION_S", "0")
    monkeypatch.setenv("COMPLETION_POLL_SECONDS", "0.02")
    store, producer, notifier = fakes
    app = create_app(store=store, producer=producer, notifier=notifier, start_consumer=False)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        client.app = app
        yield client


async def test_the_scheduler_completes_trips_in_the_background(http, fakes):
    _, producer, _ = fakes
    event = matched()
    await http.app.state.deps["trips"].on_ride_matched(event)
    for _ in range(100):
        if producer.sent:
            break
        await asyncio.sleep(0.02)
    assert producer.sent[0][1] == event["ride_id"]
    trip = (await http.get(f"/trips/{event['ride_id']}")).json()
    assert trip["status"] == "completed"


async def test_trip_lookup_errors(http):
    assert (await http.get(f"/trips/{uuid.uuid4()}")).status_code == 404
    assert (await http.get("/trips/nope")).status_code == 422


async def test_readiness_covers_postgres_and_nats(http, fakes):
    _, _, notifier = fakes
    http.app.state.health.cache_seconds = 0
    assert (await http.get("/readyz")).json()["checks"] == {"postgres": "ok", "nats": "ok"}
    notifier.down = True
    ready = await http.get("/readyz")
    assert ready.status_code == 503 and ready.json()["checks"]["nats"].startswith("failed")
