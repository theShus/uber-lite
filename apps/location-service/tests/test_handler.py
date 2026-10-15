"""The message handler, against an in-memory Valkey."""

import fakeredis
import httpx
import pytest

from app.core.kafka import PoisonMessage
from app.main import GEO_KEY, create_app, make_handler


@pytest.fixture
def valkey():
    return fakeredis.FakeRedis()


def test_a_ping_updates_the_geo_index_and_last_seen(valkey):
    handle = make_handler(valkey, ttl_seconds=60)
    handle({"driver_id": "d1", "lat": 52.52, "lon": 13.40, "ts": "2026-10-15T12:00:00Z"}, None)
    lon, lat = valkey.geopos(GEO_KEY, "d1")[0]
    assert lat == pytest.approx(52.52, abs=1e-4) and lon == pytest.approx(13.40, abs=1e-4)
    assert valkey.get("driver:last_seen:d1") == b"2026-10-15T12:00:00Z"
    assert 0 < valkey.ttl("driver:last_seen:d1") <= 60


def test_the_latest_ping_wins(valkey):
    handle = make_handler(valkey, ttl_seconds=60)
    handle({"driver_id": "d1", "lat": 52.52, "lon": 13.40}, None)
    handle({"driver_id": "d1", "lat": 52.50, "lon": 13.45}, None)
    assert valkey.zcard(GEO_KEY) == 1
    _, lat = valkey.geopos(GEO_KEY, "d1")[0]
    assert lat == pytest.approx(52.50, abs=1e-4)


@pytest.mark.parametrize(
    "event",
    [
        {"lat": 52.5, "lon": 13.4},  # no driver
        {"driver_id": "d1", "lat": "north", "lon": 13.4},
        {"driver_id": "d1", "lat": 89.9, "lon": 13.4},  # beyond what GEO can store
    ],
)
def test_bad_events_are_poison(valkey, event):
    with pytest.raises(PoisonMessage):
        make_handler(valkey, ttl_seconds=60)(event, None)


def test_valkey_errors_raise_so_the_message_is_retried():
    server = fakeredis.FakeServer()
    server.connected = False
    handle = make_handler(fakeredis.FakeRedis(server=server), ttl_seconds=60)
    with pytest.raises(Exception):  # noqa: B017 (any error: the worker retries)
        handle({"driver_id": "d1", "lat": 52.5, "lon": 13.4}, None)


async def test_http_endpoints(valkey):
    app = create_app(valkey=valkey, start_consumer=False)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://l") as http:
        assert (await http.get("/healthz")).status_code == 200
        ready = await http.get("/readyz")
        assert ready.status_code == 200 and ready.json()["checks"] == {"valkey": "ok"}
        assert "location_updates_total" in (await http.get("/metrics")).text
