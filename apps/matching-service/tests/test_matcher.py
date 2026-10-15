"""Matching rules, against an in-memory Valkey and a fake producer."""

import fakeredis
import httpx
import pytest

from app.core.kafka import PoisonMessage
from app.main import create_app
from app.matcher import GEO_KEY, Matcher

PICKUP = {"lat": 52.5200, "lon": 13.4000}


def ride(ride_id="ride-1", pickup=PICKUP):
    return {
        "ride_id": ride_id,
        "rider_id": "r1",
        "pickup": pickup,
        "dropoff": {"lat": 52.50, "lon": 13.45},
        "price": {"amount_cents": 736, "currency": "EUR"},
    }


class FakeProducer:
    def __init__(self):
        self.sent = []
        self.down = False

    def send_sync(self, topic, key, value):
        if self.down:
            raise TimeoutError("no ack")
        self.sent.append((topic, key, value))

    def close(self):
        pass


@pytest.fixture
def valkey():
    return fakeredis.FakeRedis()


@pytest.fixture
def producer():
    return FakeProducer()


@pytest.fixture
def matcher(valkey, producer):
    return Matcher(valkey, producer, busy_ttl=60)


def online(valkey, driver_id, lat, lon):
    valkey.geoadd(GEO_KEY, (lon, lat, driver_id))
    valkey.set(f"driver:last_seen:{driver_id}", "now", ex=60)


def test_nearest_online_driver_is_matched(matcher, valkey, producer):
    online(valkey, "far", 52.5300, 13.4000)  # ~1.1 km
    online(valkey, "near", 52.5205, 13.4000)  # ~55 m
    matcher.handle(ride(), None)
    topic, key, event = producer.sent[0]
    assert (topic, key) == ("ride.matched", "ride-1")
    assert event["driver_id"] == "near"
    assert event["distance_km"] == pytest.approx(0.056, abs=0.01)
    assert event["price"] == {"amount_cents": 736, "currency": "EUR"}
    assert valkey.get("busy:near") == b"ride-1"
    assert 0 < valkey.ttl("busy:near") <= 60


def test_busy_drivers_are_skipped(matcher, valkey, producer):
    online(valkey, "near", 52.5205, 13.4000)
    online(valkey, "other", 52.5250, 13.4000)
    valkey.set("busy:near", "another-ride")
    matcher.handle(ride(), None)
    assert producer.sent[0][2]["driver_id"] == "other"


def test_offline_drivers_are_removed_from_the_index(matcher, valkey, producer):
    valkey.geoadd(GEO_KEY, (13.4000, 52.5205, "ghost"))  # no last_seen key: offline
    online(valkey, "real", 52.5250, 13.4000)
    matcher.handle(ride(), None)
    assert producer.sent[0][2]["driver_id"] == "real"
    assert valkey.geopos(GEO_KEY, "ghost") == [None]


def test_the_search_widens_to_10_km(matcher, valkey, producer):
    online(valkey, "suburb", 52.5800, 13.4000)  # ~6.7 km
    matcher.handle(ride(), None)
    assert producer.sent[0][2]["driver_id"] == "suburb"


def test_no_driver_means_no_event(matcher, producer):
    matcher.handle(ride(), None)
    assert producer.sent == []


def test_a_redelivered_ride_gets_the_same_driver(matcher, valkey, producer):
    online(valkey, "near", 52.5205, 13.4000)
    online(valkey, "other", 52.5250, 13.4000)
    matcher.handle(ride(), None)
    matcher.handle(ride(), None)  # Kafka delivered it again
    assert [e["driver_id"] for _, _, e in producer.sent] == ["near", "near"]
    assert valkey.get("busy:other") is None  # no second driver was claimed


def test_kafka_failure_raises_and_the_retry_keeps_the_match(matcher, valkey, producer):
    online(valkey, "near", 52.5205, 13.4000)
    producer.down = True
    with pytest.raises(TimeoutError):
        matcher.handle(ride(), None)  # the worker will retry this message
    producer.down = False
    matcher.handle(ride(), None)
    assert producer.sent[0][2]["driver_id"] == "near"


@pytest.mark.parametrize("bad", [{"ride_id": "x"}, {**ride(), "pickup": {"lat": "here"}}])
def test_bad_events_are_poison(matcher, bad):
    with pytest.raises(PoisonMessage):
        matcher.handle(bad, None)


async def test_http_endpoints(valkey, producer):
    app = create_app(valkey=valkey, producer=producer, start_consumer=False)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://m") as http:
        assert (await http.get("/readyz")).json()["checks"] == {"valkey": "ok"}
        assert "rides_matched_total" in (await http.get("/metrics")).text
