"""rider-api's HTTP behaviour, with in-memory fakes for Postgres, pricing and Kafka."""

import asyncio

import httpx
import pytest

from app.main import create_app
from app.pricing_client import PricingUnavailable, channel_options
from app.proto import pricing_pb2

RIDE = {"rider_id": "r1", "pickup": {"lat": 52.52, "lon": 13.40}, "dropoff": {"lat": 52.50, "lon": 13.45}}


class FakeStore:
    def __init__(self):
        self.rides: dict[str, dict] = {}
        self.outbox: list[tuple[str, str, dict]] = []
        self.down = False

    async def check(self):
        if self.down:
            raise ConnectionError("postgres is down")

    async def create(self, ride, topic, event):
        await self.check()
        self.rides[ride["id"]] = ride
        self.outbox.append((topic, ride["id"], event))

    async def get(self, ride_id):
        return self.rides.get(ride_id)

    async def relay_outbox(self, send, batch=100):
        rows, self.outbox = self.outbox[:batch], self.outbox[batch:]
        try:
            await asyncio.gather(*(send(t, k, v) for t, k, v in rows))
        except Exception:
            self.outbox = rows + self.outbox  # the transaction rolled back
            raise
        return len(rows)

    async def outbox_size(self):
        return len(self.outbox)

    async def close(self):
        pass


class FakeProducer:
    def __init__(self):
        self.sent: list[tuple[str, str, dict]] = []
        self.down = False

    async def send(self, topic, key, value):
        if self.down:
            raise RuntimeError("kafka is down")
        self.sent.append((topic, key, value))

    def close(self):
        pass


class FakePricing:
    def __init__(self):
        self.down = False

    async def quote(self, rider_id, pickup, dropoff):
        if self.down:
            raise PricingUnavailable("UNAVAILABLE: connection refused")
        return pricing_pb2.QuoteReply(quote_id="q1", amount_cents=557, currency="EUR", surge=1.2, distance_km=2.56)

    async def close(self):
        pass


@pytest.fixture
def fakes():
    return FakeStore(), FakePricing(), FakeProducer()


@pytest.fixture
async def http(fakes):
    store, pricing, producer = fakes
    app = create_app(store=store, pricing=pricing, producer=producer)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://r") as client:
        client.health = app.state.health  # so tests can switch off the readiness cache
        yield client


async def wait_for(condition, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_request_a_ride(http, fakes):
    store, _, producer = fakes
    response = await http.post("/rides", json=RIDE)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "requested"
    assert body["price"] == {"amount_cents": 557, "currency": "EUR"}
    ride_id = body["ride_id"]
    assert store.rides[ride_id]["rider_id"] == "r1"

    await wait_for(lambda: producer.sent)  # the outbox relay published it
    topic, key, event = producer.sent[0]
    assert (topic, key) == ("ride.requested", ride_id)
    assert event["pickup"] == RIDE["pickup"] and event["price"]["amount_cents"] == 557
    assert store.outbox == []


async def test_kafka_down_keeps_the_event_in_the_outbox(http, fakes):
    store, _, producer = fakes
    producer.down = True
    response = await http.post("/rides", json=RIDE)
    assert response.status_code == 202  # the ride is safe in Postgres
    await asyncio.sleep(0.1)
    assert len(store.outbox) == 1 and producer.sent == []

    producer.down = False  # Kafka is back
    await wait_for(lambda: producer.sent, timeout=3)  # the relay retries on its own
    assert store.outbox == []


async def test_pricing_down_is_503_and_nothing_is_stored(http, fakes):
    store, pricing, _ = fakes
    pricing.down = True
    response = await http.post("/rides", json=RIDE)
    assert response.status_code == 503
    assert store.rides == {}


async def test_database_down_is_503(http, fakes):
    store, _, _ = fakes
    store.down = True
    assert (await http.post("/rides", json=RIDE)).status_code == 503


@pytest.mark.parametrize(
    "bad",
    [
        {**RIDE, "pickup": {"lat": 100, "lon": 13.4}},
        {**RIDE, "rider_id": ""},
        {"rider_id": "r1", "pickup": {"lat": 52.5, "lon": 13.4}},
    ],
)
async def test_invalid_requests_are_422(http, bad):
    assert (await http.post("/rides", json=bad)).status_code == 422


async def test_get_a_ride(http):
    ride_id = (await http.post("/rides", json=RIDE)).json()["ride_id"]
    response = await http.get(f"/rides/{ride_id}")
    assert response.status_code == 200
    assert response.json()["amount_cents"] == 557


async def test_unknown_and_malformed_ride_ids(http):
    assert (await http.get("/rides/00000000-0000-0000-0000-000000000000")).status_code == 404
    assert (await http.get("/rides/not-a-uuid")).status_code == 422


async def test_get_on_the_collection_is_405(http):
    assert (await http.get("/rides")).status_code == 405


async def test_readiness_follows_postgres_not_pricing(http, fakes):
    store, pricing, _ = fakes
    http.health.cache_seconds = 0  # results are normally cached for 2 s
    pricing.down = True
    assert (await http.get("/readyz")).status_code == 200  # pricing isn't a readiness dependency
    store.down = True
    ready = await http.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"]["postgres"].startswith("failed")


async def test_metrics_use_route_templates(http):
    ride_id = (await http.post("/rides", json=RIDE)).json()["ride_id"]
    await http.get(f"/rides/{ride_id}")
    body = (await http.get("/metrics")).text
    assert 'http_requests_total{method="POST",route="/rides",status="202"}' in body
    assert 'route="/rides/{ride_id}"' in body  # the template, never the raw id
    assert ride_id not in body
    assert "rides_requested_total" in body


def test_round_robin_only_for_dns_targets():
    assert channel_options("pricing-service:50051") == []
    assert channel_options("dns:///pricing-service-headless.uber-lite.svc.cluster.local:50051") == [
        ("grpc.lb_policy_name", "round_robin")
    ]
