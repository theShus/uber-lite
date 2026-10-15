"""driver-api: drivers report where they are, many times a minute.

POST /drivers/{driver_id}/location  → Kafka driver.locations, key = driver_id

Run: uvicorn app.main:app --host 0.0.0.0 --port 8080
"""

import contextlib
import datetime

from fastapi import FastAPI, HTTPException, Path
from prometheus_client import Counter
from pydantic import BaseModel, Field

from .core.config import env_float
from .core.health import Health, health_router
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import setup_tracing, shutdown_tracing

SERVICE = "driver-api"
TOPIC = "driver.locations"
log = setup_logging(SERVICE)

LOCATIONS = Counter("driver_locations_accepted_total", "Location updates queued for Kafka")
BACKPRESSURE = Counter("driver_locations_rejected_total", "Location updates refused because the producer was full")


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


def create_app(producer=None, kafka_check=None) -> FastAPI:
    health = Health()
    deps: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if producer is None:
            from .core.kafka import KafkaProducer, readiness_check

            deps["producer"] = KafkaProducer(SERVICE)
            health.add_blocking_check("kafka", readiness_check(SERVICE, [TOPIC]))
        else:
            deps["producer"] = producer
            if kafka_check:
                health.add_check("kafka", kafka_check)
        install_shutdown_hook(health, env_float("SHUTDOWN_DELAY_SECONDS", 0))
        log.info("started")
        try:
            yield
        finally:
            health.shutting_down = True
            deps["producer"].close()  # flush what's still queued
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health

    @app.post("/drivers/{driver_id}/location", status_code=202)
    async def report_location(body: Location, driver_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")):
        event = {
            "driver_id": driver_id,
            "lat": body.lat,
            "lon": body.lon,
            "ts": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        try:
            # Fire-and-forget: queued in librdkafka, delivered in batches (KAFKA_LINGER_MS).
            # Losing one ping is harmless: the next one replaces it a few seconds later.
            deps["producer"].produce(TOPIC, driver_id, event)
        except BufferError as exc:
            # The local queue is full: Kafka is slow or down. Refuse instead of buffering without limit.
            BACKPRESSURE.inc()
            raise HTTPException(503, "busy: the Kafka producer queue is full", headers={"Retry-After": "1"}) from exc
        LOCATIONS.inc()
        return {"status": "accepted"}

    if setup_tracing(SERVICE):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    return app


app = create_app()
