"""matching-service: Kafka ride.requested (group matching-service) → nearest free driver → Kafka ride.matched.

Run: uvicorn app.main:app --host 0.0.0.0 --port 8080   (HTTP only serves health and metrics)
"""

import contextlib

import redis
from fastapi import FastAPI

from .core.config import env_float, env_int, env_str
from .core.health import Health, health_router
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import setup_tracing, shutdown_tracing
from .matcher import OUT_TOPIC, Matcher

SERVICE = "matching-service"
IN_TOPIC = "ride.requested"
GROUP = "matching-service"
log = setup_logging(SERVICE)


def create_app(valkey: redis.Redis | None = None, producer=None, start_consumer: bool = True) -> FastAPI:
    health = Health()
    deps: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        client = valkey or redis.Redis.from_url(
            env_str("VALKEY_URL"), socket_timeout=2, socket_connect_timeout=2, health_check_interval=30
        )
        if producer is None:
            from .core.kafka import KafkaProducer

            deps["producer"] = KafkaProducer(SERVICE)
        else:
            deps["producer"] = producer
        matcher = Matcher(client, deps["producer"], busy_ttl=env_int("BUSY_TTL_SECONDS", 60))
        deps["matcher"] = matcher
        health.add_blocking_check("valkey", client.ping)
        worker = None
        if start_consumer:
            from .core.kafka import ConsumerWorker, readiness_check

            worker = ConsumerWorker(SERVICE, GROUP, IN_TOPIC, matcher.handle)
            health.add_blocking_check("kafka", readiness_check(SERVICE, [IN_TOPIC, OUT_TOPIC]))
            health.add_check("consumer", worker.alive_check)
            worker.start()
        install_shutdown_hook(health, env_float("SHUTDOWN_DELAY_SECONDS", 0))
        log.info("started", extra={"group": GROUP, "topic": IN_TOPIC})
        try:
            yield
        finally:
            health.shutting_down = True
            if worker:
                worker.stop()
            deps["producer"].close()
            client.close()
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health
    app.state.deps = deps

    if setup_tracing(SERVICE):
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    return app


app = create_app()
