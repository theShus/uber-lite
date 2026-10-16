"""payment-service: Kafka trip.completed (group payment-service) → charge (fake PSP) → Kafka payment.settled.

Run: uvicorn app.main:app --host 0.0.0.0 --port 8080   (HTTP only serves health and metrics)
"""

import contextlib

from fastapi import FastAPI

from .core.config import env_float, env_int
from .core.health import Health, health_router
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import FASTAPI_TELEMETRY, setup_tracing, shutdown_tracing
from .payments import SETTLED_TOPIC, PaymentProcessor
from .psp import FakePSP

SERVICE = "payment-service"
IN_TOPIC = "trip.completed"
GROUP = "payment-service"
log = setup_logging(SERVICE)


def create_app(store=None, producer=None, start_consumer: bool = True) -> FastAPI:
    health = Health()
    deps: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if store is None:
            from .core import db
            from .payments import PgPaymentStore

            pool = db.sync_pool()  # the consumer thread is synchronous, so is its pool
            pool.open(wait=False)
            deps["store"] = PgPaymentStore(pool)
        else:
            deps["store"] = store
        if producer is None:
            from .core.kafka import KafkaProducer

            deps["producer"] = KafkaProducer(SERVICE)
        else:
            deps["producer"] = producer
        psp = FakePSP(latency_ms=env_float("PSP_LATENCY_MS", 150), failure_rate=env_float("PSP_FAILURE_RATE", 0.02))
        processor = PaymentProcessor(deps["store"], deps["producer"], psp, max_attempts=env_int("PSP_MAX_ATTEMPTS", 3))
        deps["processor"] = processor
        health.add_blocking_check("postgres", deps["store"].check)
        worker = None
        if start_consumer:
            from .core.kafka import ConsumerWorker, readiness_check

            worker = ConsumerWorker(SERVICE, GROUP, IN_TOPIC, processor.handle)
            health.add_blocking_check("kafka", readiness_check(SERVICE, [IN_TOPIC, SETTLED_TOPIC]))
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
            deps["store"].close()
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan, telemetry=FASTAPI_TELEMETRY)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health
    app.state.deps = deps

    if setup_tracing(SERVICE):
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        PsycopgInstrumentor().instrument()
    return app


app = create_app()
