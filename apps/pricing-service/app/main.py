"""pricing-service: gRPC Quote on 50051, health and metrics over HTTP on 8080, in one process.

Run: uvicorn app.main:app --host 0.0.0.0 --port 8080
The gRPC server starts and stops with the HTTP app (its lifespan).
"""

import contextlib

from fastapi import FastAPI
from redis.asyncio import Redis

from . import grpc_server
from .core.config import env_float, env_int, env_str
from .core.health import Health, health_router
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import FASTAPI_TELEMETRY, setup_tracing, shutdown_tracing

SERVICE = "pricing-service"
log = setup_logging(SERVICE)


def create_app(valkey: Redis | None = None, grpc_port: int | None = None) -> FastAPI:
    """Tests pass a fake Valkey client and a free port; uvicorn uses the defaults from the environment."""
    health = Health()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        client = valkey or Redis.from_url(
            env_str("VALKEY_URL"), socket_timeout=1, socket_connect_timeout=1, health_check_interval=30
        )
        health.add_check("valkey", client.ping)
        port = grpc_port or env_int("GRPC_PORT", 50051)
        server, grpc_health = grpc_server.build_server(client, port)
        await server.start()
        await grpc_health.set("", grpc_server.SERVING)
        await grpc_health.set("uberlite.pricing.v1.Pricing", grpc_server.SERVING)
        install_shutdown_hook(health, env_float("SHUTDOWN_DELAY_SECONDS", 0))
        app.state.valkey = client
        log.info("started", extra={"grpc_port": port})
        try:
            yield
        finally:
            health.shutting_down = True
            await grpc_health.enter_graceful_shutdown()  # gRPC health → NOT_SERVING
            await server.stop(grace=10)  # in-flight calls get up to 10 s
            await client.aclose()
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan, telemetry=FASTAPI_TELEMETRY)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health

    if setup_tracing(SERVICE):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.grpc import GrpcAioInstrumentorServer
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
        GrpcAioInstrumentorServer().instrument()
        RedisInstrumentor().instrument()
    return app


app = create_app()
