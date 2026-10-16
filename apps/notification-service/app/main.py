"""notification-service: NATS JetStream notify.> → "send" push notifications (logs them).

Scales to zero with KEDA when nothing is pending (topic 21).
Run: uvicorn app.main:app --host 0.0.0.0 --port 8080   (HTTP only serves health and metrics)
"""

import asyncio
import contextlib

from fastapi import FastAPI

from .consumer import NotificationConsumer
from .core.config import env_float, env_int, env_str
from .core.health import Health, health_router
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import FASTAPI_TELEMETRY, setup_tracing, shutdown_tracing

SERVICE = "notification-service"
log = setup_logging(SERVICE)


def create_app(start_consumer: bool = True) -> FastAPI:
    health = Health()
    deps: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if start_consumer:
            consumer = NotificationConsumer(
                env_str("NATS_URL"), SERVICE, delay_s=env_int("PROCESSING_DELAY_MS", 0) / 1000
            )
            deps["consumer"] = consumer
            health.add_check("nats", consumer.check)
            task = asyncio.create_task(consumer.run())
        install_shutdown_hook(health, env_float("SHUTDOWN_DELAY_SECONDS", 0))
        log.info("started")
        try:
            yield
        finally:
            health.shutting_down = True
            if task:
                await deps["consumer"].stop()  # stop fetching; drain acks what's in flight
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan, telemetry=FASTAPI_TELEMETRY)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health
    app.state.deps = deps
    setup_tracing(SERVICE)
    return app


app = create_app()
