"""driver-api's HTTP behaviour, with a fake Kafka producer."""

import httpx
import pytest

from app.main import create_app


class FakeProducer:
    def __init__(self):
        self.queued: list[tuple[str, str, dict]] = []
        self.full = False

    def produce(self, topic, key, value):
        if self.full:
            raise BufferError("Local: Queue full")
        self.queued.append((topic, key, value))

    def close(self):
        pass


class FakeKafkaCheck:
    def __init__(self):
        self.down = False

    def __call__(self):
        if self.down:
            raise ConnectionError("all brokers down")


@pytest.fixture
def producer():
    return FakeProducer()


@pytest.fixture
def kafka():
    return FakeKafkaCheck()


@pytest.fixture
async def http(producer, kafka):
    app = create_app(producer=producer, kafka_check=kafka)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://d") as client:
        client.health = app.state.health  # so tests can clear the readiness cache
        yield client


async def test_location_is_queued_keyed_by_driver(http, producer):
    response = await http.post("/drivers/d-42/location", json={"lat": 52.5, "lon": 13.4})
    assert response.status_code == 202
    topic, key, event = producer.queued[0]
    assert (topic, key) == ("driver.locations", "d-42")
    assert event["driver_id"] == "d-42" and event["lat"] == 52.5 and "ts" in event


async def test_full_queue_is_503_with_retry_after(http, producer):
    producer.full = True
    response = await http.post("/drivers/d1/location", json={"lat": 52.5, "lon": 13.4})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"


@pytest.mark.parametrize(
    ("driver_id", "body"),
    [
        ("d1", {"lat": 91, "lon": 13.4}),
        ("d1", {"lat": 52.5}),
        ("bad id!", {"lat": 52.5, "lon": 13.4}),
        ("x" * 65, {"lat": 52.5, "lon": 13.4}),
    ],
)
async def test_invalid_input_is_422(http, driver_id, body):
    assert (await http.post(f"/drivers/{driver_id}/location", json=body)).status_code == 422


async def test_readiness_follows_kafka(http, kafka):
    assert (await http.get("/readyz")).status_code == 200
    kafka.down = True
    http.health.cache_seconds = 0  # results are normally cached for 2 s
    response = await http.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["kafka"].startswith("failed")


async def test_metrics(http):
    await http.post("/drivers/d1/location", json={"lat": 52.5, "lon": 13.4})
    body = (await http.get("/metrics")).text
    assert "driver_locations_accepted_total" in body
    assert 'route="/drivers/{driver_id}/location"' in body
