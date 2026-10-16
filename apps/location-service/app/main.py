"""location-service: keeps the live geo index of drivers.

Kafka driver.locations (group location-service) → Valkey:
  GEOADD drivers:geo <lon> <lat> <driver_id>        where every driver is
  SET driver:last_seen:<driver_id> <ts> EX 60       who is online (matching skips the rest)

Run: uvicorn app.main:app --host 0.0.0.0 --port 8080   (HTTP only serves health and metrics)
"""

import contextlib

import redis
from fastapi import FastAPI
from prometheus_client import Counter

from .core.config import env_float, env_int, env_str
from .core.health import Health, health_router
from .core.kafka import PoisonMessage
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import FASTAPI_TELEMETRY, setup_tracing, shutdown_tracing

SERVICE = "location-service"
TOPIC = "driver.locations"
GROUP = "location-service"
GEO_KEY = "drivers:geo"
MAX_GEO_LAT = 85.05112878  # Valkey's GEO index can't store latitudes beyond this
log = setup_logging(SERVICE)

LOCATION_UPDATES = Counter("location_updates_total", "Driver positions written to the geo index")


def make_handler(valkey: redis.Redis, ttl_seconds: int):
    def handle(event: dict, _msg) -> None:
        try:
            driver_id = str(event["driver_id"])
            lat, lon = float(event["lat"]), float(event["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PoisonMessage(f"bad location event: {exc}") from exc
        if not (-MAX_GEO_LAT <= lat <= MAX_GEO_LAT and -180 <= lon <= 180):
            raise PoisonMessage(f"position out of range: {lat}, {lon}")
        pipe = valkey.pipeline(transaction=False)
        pipe.geoadd(GEO_KEY, (lon, lat, driver_id))
        pipe.set(f"driver:last_seen:{driver_id}", event.get("ts", ""), ex=ttl_seconds)
        pipe.execute()  # a Valkey error raises: the worker retries the same message
        LOCATION_UPDATES.inc()

    return handle


def create_app(valkey: redis.Redis | None = None, start_consumer: bool = True) -> FastAPI:
    """Tests pass a fake Valkey and start_consumer=False, and call the handler directly."""
    health = Health()
    deps: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        client = valkey or redis.Redis.from_url(
            env_str("VALKEY_URL"), socket_timeout=2, socket_connect_timeout=2, health_check_interval=30
        )
        deps["valkey"] = client
        deps["handler"] = make_handler(client, env_int("DRIVER_TTL_SECONDS", 60))
        health.add_blocking_check("valkey", client.ping)
        worker = None
        if start_consumer:
            from .core.kafka import ConsumerWorker, readiness_check

            worker = ConsumerWorker(SERVICE, GROUP, TOPIC, deps["handler"])
            health.add_blocking_check("kafka", readiness_check(SERVICE, [TOPIC]))
            health.add_check("consumer", worker.alive_check)
            worker.start()
        install_shutdown_hook(health, env_float("SHUTDOWN_DELAY_SECONDS", 0))
        log.info("started", extra={"group": GROUP, "topic": TOPIC})
        try:
            yield
        finally:
            health.shutting_down = True
            if worker:
                worker.stop()  # finishes the message in hand, commits, leaves the group
            client.close()
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan, telemetry=FASTAPI_TELEMETRY)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health
    app.state.deps = deps

    if setup_tracing(SERVICE):
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    return app


app = create_app()
