"""rider-api: riders request rides.

POST /rides          price it (gRPC to pricing), store it with its event (outbox), 202
GET  /rides/{id}     the stored ride

Run: uvicorn app.main:app --host 0.0.0.0 --port 8080
"""

import asyncio
import contextlib
import datetime
import uuid

from fastapi import FastAPI, HTTPException
from prometheus_client import Counter
from pydantic import BaseModel, Field

from .core.config import env_float, env_str
from .core.health import Health, health_router
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import FASTAPI_TELEMETRY, setup_tracing, shutdown_tracing
from .pricing_client import PricingClient, PricingUnavailable
from .store import OutboxRelay

SERVICE = "rider-api"
TOPIC = "ride.requested"
log = setup_logging(SERVICE)

RIDES_REQUESTED = Counter("rides_requested_total", "Rides accepted (stored, event queued)")
RIDES_REJECTED = Counter("rides_rejected_total", "Ride requests that failed", ["reason"])


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RideRequest(BaseModel):
    rider_id: str = Field(min_length=1, max_length=64)
    pickup: Point
    dropoff: Point


def create_app(store=None, pricing=None, producer=None, kafka_check=None) -> FastAPI:
    """Tests inject fakes; in production every dependency is built from the environment in the lifespan."""
    health = Health()
    deps: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if store is None:
            from .core import db
            from .store import PgRideStore

            pool = db.async_pool()
            await pool.open(wait=False)  # start even if Postgres isn't up yet: readiness says so
            deps["store"] = PgRideStore(pool)
        else:
            deps["store"] = store
        deps["pricing"] = pricing or PricingClient(env_str("PRICING_ADDR"))
        if producer is None:
            from .core.kafka import KafkaProducer, readiness_check

            deps["producer"] = KafkaProducer(SERVICE)
            health.add_blocking_check("kafka", readiness_check(SERVICE, [TOPIC]))
        else:
            deps["producer"] = producer
            if kafka_check:
                health.add_check("kafka", kafka_check)
        # Readiness covers what this pod can't work without. NOT pricing: a pricing outage
        # fails ride requests fast (503) instead of making every rider-api pod unready.
        health.add_check("postgres", deps["store"].check)

        relay = OutboxRelay(deps["store"], deps["producer"].send)
        deps["relay"] = relay
        relay_task = asyncio.create_task(relay.run())
        install_shutdown_hook(health, env_float("SHUTDOWN_DELAY_SECONDS", 0))
        log.info("started")
        try:
            yield
        finally:
            health.shutting_down = True
            relay.stop()
            await relay_task  # finish the batch in flight
            await deps["pricing"].close()
            deps["producer"].close()
            await deps["store"].close()
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan, telemetry=FASTAPI_TELEMETRY)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health
    app.state.deps = deps

    @app.post("/rides", status_code=202)
    async def request_ride(body: RideRequest):
        try:
            quote = await deps["pricing"].quote(body.rider_id, body.pickup.model_dump(), body.dropoff.model_dump())
        except PricingUnavailable as exc:
            RIDES_REJECTED.labels("pricing").inc()
            log.warning("pricing unavailable", extra={"rider_id": body.rider_id, "error": str(exc)})
            raise HTTPException(503, "pricing unavailable, try again") from exc

        ride_id = str(uuid.uuid4())
        price = {"amount_cents": quote.amount_cents, "currency": quote.currency}
        ride = {
            "id": ride_id,
            "rider_id": body.rider_id,
            "pickup_lat": body.pickup.lat,
            "pickup_lon": body.pickup.lon,
            "dropoff_lat": body.dropoff.lat,
            "dropoff_lon": body.dropoff.lon,
            "amount_cents": quote.amount_cents,
            "currency": quote.currency,
            "surge": quote.surge,
            "quote_id": quote.quote_id,
        }
        event = {
            "ride_id": ride_id,
            "rider_id": body.rider_id,
            "pickup": body.pickup.model_dump(),
            "dropoff": body.dropoff.model_dump(),
            "price": price,
            "surge": quote.surge,
            "requested_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        try:
            await deps["store"].create(ride, TOPIC, event)
        except Exception as exc:
            RIDES_REJECTED.labels("database").inc()
            log.error("storing the ride failed", extra={"rider_id": body.rider_id, "error": str(exc)})
            raise HTTPException(503, "database unavailable, try again") from exc
        deps["relay"].wake()
        RIDES_REQUESTED.inc()
        log.info(
            "ride requested",
            extra={
                "ride_id": ride_id,
                "rider_id": body.rider_id,
                "amount_cents": quote.amount_cents,
                "surge": quote.surge,
            },
        )
        return {"ride_id": ride_id, "status": "requested", "price": price, "surge": quote.surge}

    @app.get("/rides/{ride_id}")
    async def get_ride(ride_id: uuid.UUID):
        ride = await deps["store"].get(str(ride_id))
        if ride is None:
            raise HTTPException(404, "no such ride")
        return ride

    if setup_tracing(SERVICE):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.grpc import GrpcAioInstrumentorClient
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
        GrpcAioInstrumentorClient().instrument()
        PsycopgInstrumentor().instrument()
    return app


app = create_app()
