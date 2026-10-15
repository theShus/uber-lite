"""The running service: gRPC Quote, gRPC health, reflection, and the HTTP endpoints, against a fake Valkey."""

import socket

import fakeredis
import grpc
import httpx
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from app.main import create_app
from app.proto import pricing_pb2, pricing_pb2_grpc


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_server():
    return fakeredis.FakeServer()  # fake_server.connected = False simulates Valkey going away


@pytest.fixture
def valkey(fake_server):
    return fakeredis.FakeAsyncRedis(server=fake_server)


@pytest.fixture
async def service(valkey):
    port = free_port()
    app = create_app(valkey=valkey, grpc_port=port)
    async with app.router.lifespan_context(app):  # starts the gRPC server, on this test's event loop
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pricing") as http:
            yield http, f"127.0.0.1:{port}"


def berlin_request(rider="r1"):
    return pricing_pb2.QuoteRequest(
        rider_id=rider,
        pickup=pricing_pb2.Point(lat=52.5219, lon=13.4132),
        dropoff=pricing_pb2.Point(lat=52.4991, lon=13.4180),
    )


async def quote(target, request):
    async with grpc.aio.insecure_channel(target) as channel:
        return await pricing_pb2_grpc.PricingStub(channel).Quote(request, timeout=5)


async def test_healthz_and_readyz(service):
    http, _ = service
    assert (await http.get("/healthz")).json() == {"status": "ok"}
    ready = await http.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"valkey": "ok"}


async def test_readyz_is_503_when_valkey_is_down(service, fake_server):
    http, _ = service
    fake_server.connected = False
    ready = await http.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"]["valkey"].startswith("failed")
    assert (await http.get("/healthz")).status_code == 200  # liveness never checks dependencies


async def test_metrics_endpoint(service):
    http, target = service
    await quote(target, berlin_request())
    body = (await http.get("/metrics")).text
    assert 'grpc_server_handled_total{code="OK",method="Quote"}' in body  # counters are process-wide: no exact count


async def test_quote_without_drivers_is_surged(service):
    _, target = service
    reply = await quote(target, berlin_request())
    assert reply.currency == "EUR"
    assert reply.distance_km == pytest.approx(2.56, abs=0.05)
    # demand 1, supply 0 (counted as 1): ratio 1 → no surge yet
    assert reply.surge == 1.0
    assert reply.amount_cents == 557  # 250 + 120 × 2.56, rounded
    assert reply.quote_id


async def test_demand_raises_the_surge(service, valkey):
    _, target = service
    for i in range(9):
        await quote(target, berlin_request(f"r{i}"))
    reply = await quote(target, berlin_request("r9"))  # demand 10, supply 0 → ratio 10 → 3.0
    assert reply.surge == 3.0
    assert await valkey.ttl("demand:cell:1050:268") > 0  # the window expires


async def test_drivers_nearby_lower_the_surge(service, valkey):
    _, target = service
    for i in range(10):
        await valkey.geoadd("drivers:geo", (13.4132 + i * 0.001, 52.5219, f"d{i}"))
    for i in range(10):
        reply = await quote(target, berlin_request(f"r{i}"))
    assert reply.surge == 1.0  # 10 requests, 10 drivers


async def test_invalid_coordinates_are_rejected(service):
    _, target = service
    bad = pricing_pb2.QuoteRequest(pickup=pricing_pb2.Point(lat=95, lon=0), dropoff=pricing_pb2.Point(lat=0, lon=0))
    with pytest.raises(grpc.aio.AioRpcError) as err:
        await quote(target, bad)
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_valkey_down_is_unavailable(service, fake_server):
    _, target = service
    fake_server.connected = False
    with pytest.raises(grpc.aio.AioRpcError) as err:
        await quote(target, berlin_request())
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE


async def test_grpc_health_is_serving(service):
    _, target = service
    async with grpc.aio.insecure_channel(target) as channel:
        reply = await health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(service="uberlite.pricing.v1.Pricing"), timeout=5
        )
    assert reply.status == health_pb2.HealthCheckResponse.SERVING


async def test_reflection_lists_the_pricing_service(service):
    from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

    _, target = service
    async with grpc.aio.insecure_channel(target) as channel:
        stub = reflection_pb2_grpc.ServerReflectionStub(channel)
        responses = stub.ServerReflectionInfo(iter([reflection_pb2.ServerReflectionRequest(list_services="")]))
        names = [s.name async for r in responses for s in r.list_services_response.service]
    assert "uberlite.pricing.v1.Pricing" in names
