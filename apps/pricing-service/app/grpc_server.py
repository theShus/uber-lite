"""The gRPC side: the Pricing service, gRPC health, server reflection, metrics."""

import logging
import time
import uuid

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_health.v1.health import aio as health_aio
from grpc_reflection.v1alpha import reflection
from prometheus_client import Counter, Histogram
from redis.asyncio import Redis

from . import pricing
from .proto import pricing_pb2, pricing_pb2_grpc

log = logging.getLogger("pricing")

GEO_KEY = "drivers:geo"  # maintained by location-service

GRPC_HANDLED = Counter("grpc_server_handled_total", "gRPC calls handled", ["method", "code"])
GRPC_DURATION = Histogram(
    "grpc_server_handling_seconds",
    "gRPC call duration",
    ["method"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
SURGE = Histogram("pricing_surge_multiplier", "Surge multiplier of each quote", buckets=(1.0, 1.2, 1.5, 2.0, 2.5, 3.0))

# Clients reconnect at least this often, so they re-resolve DNS and find pods an HPA added (16.3).
MAX_CONNECTION_AGE_MS = 5 * 60 * 1000
MAX_CONNECTION_AGE_GRACE_MS = 30 * 1000


class PricingServicer(pricing_pb2_grpc.PricingServicer):
    def __init__(self, valkey: Redis):
        self.valkey = valkey

    async def Quote(self, request, context):  # noqa: N802 (the name comes from the .proto)
        started = time.perf_counter()
        code = grpc.StatusCode.OK
        try:
            return await self._quote(request, context)
        except grpc.aio.AbortError:
            code = context.code() or grpc.StatusCode.UNKNOWN
            raise
        except Exception:
            code = grpc.StatusCode.INTERNAL
            raise
        finally:
            GRPC_HANDLED.labels("Quote", code.name).inc()
            GRPC_DURATION.labels("Quote").observe(time.perf_counter() - started)

    async def _quote(self, request, context):
        p, d = request.pickup, request.dropoff
        if not (pricing.valid_point(p.lat, p.lon) and pricing.valid_point(d.lat, d.lon)):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "pickup and dropoff need valid lat/lon")
        distance = pricing.haversine_km(p.lat, p.lon, d.lat, d.lon)
        cell = pricing.cell_of(p.lat, p.lon)
        try:
            demand, supply = await self._demand_and_supply(cell, p.lat, p.lon)
        except Exception as exc:
            log.warning("valkey unavailable", extra={"error": str(exc)})
            await context.abort(grpc.StatusCode.UNAVAILABLE, "pricing data unavailable")
        surge = pricing.surge_multiplier(demand, supply)
        amount = pricing.fare_cents(distance, surge)
        quote_id = str(uuid.uuid4())
        SURGE.observe(surge)
        log.info(
            "quote",
            extra={
                "quote_id": quote_id,
                "rider_id": request.rider_id,
                "cell": cell,
                "demand": demand,
                "supply": supply,
                "surge": surge,
                "distance_km": round(distance, 2),
                "amount_cents": amount,
            },
        )
        return pricing_pb2.QuoteReply(
            quote_id=quote_id,
            amount_cents=amount,
            currency=pricing.CURRENCY,
            surge=surge,
            distance_km=round(distance, 3),
        )

    async def _demand_and_supply(self, cell: str, lat: float, lon: float) -> tuple[int, int]:
        """Demand: quotes in this cell in the last 5 minutes (this one included). Supply: drivers within 3 km."""
        key = f"demand:{cell}"
        async with self.valkey.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, pricing.DEMAND_WINDOW_SECONDS, nx=True)  # the window starts at the first quote
            pipe.geosearch(GEO_KEY, longitude=lon, latitude=lat, radius=pricing.SUPPLY_RADIUS_KM, unit="km", count=200)
            demand, _, drivers = await pipe.execute()
        return int(demand), len(drivers)


def build_server(valkey: Redis, port: int) -> tuple[grpc.aio.Server, health_aio.HealthServicer]:
    server = grpc.aio.server(
        options=[
            ("grpc.max_connection_age_ms", MAX_CONNECTION_AGE_MS),
            ("grpc.max_connection_age_grace_ms", MAX_CONNECTION_AGE_GRACE_MS),
        ]
    )
    pricing_pb2_grpc.add_PricingServicer_to_server(PricingServicer(valkey), server)

    health = health_aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health, server)

    service_names = (
        pricing_pb2.DESCRIPTOR.services_by_name["Pricing"].full_name,
        health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)  # lets grpcurl list and call without .proto files
    server.add_insecure_port(f"[::]:{port}")
    return server, health


SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING
