"""trip-service: REST + consumer.

Kafka ride.matched (group trip-service) → a started trip in Postgres → NATS "driver on the way"
a scheduler: due trips → Kafka trip.completed → NATS "trip completed"
GET /trips/{ride_id}

Run: uvicorn app.main:app --host 0.0.0.0 --port 8080
"""

import asyncio
import contextlib
import uuid

from fastapi import FastAPI, HTTPException

from .core.config import env_float, env_str
from .core.health import Health, health_router
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import setup_tracing, shutdown_tracing
from .trips import COMPLETED_TOPIC, TripService

SERVICE = "trip-service"
IN_TOPIC = "ride.matched"
GROUP = "trip-service"
log = setup_logging(SERVICE)


def create_app(store=None, producer=None, notifier=None, start_consumer: bool = True) -> FastAPI:
    health = Health()
    deps: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        if store is None:
            from .core import db
            from .store import PgTripStore

            pool = db.async_pool()
            await pool.open(wait=False)
            deps["store"] = PgTripStore(pool)
        else:
            deps["store"] = store
        if producer is None:
            from .core.kafka import KafkaProducer

            deps["producer"] = KafkaProducer(SERVICE)
        else:
            deps["producer"] = producer
        if notifier is None:
            from .notifier import NatsNotifier

            deps["notifier"] = NatsNotifier(env_str("NATS_URL"), SERVICE)
            deps["notifier"].start()
        else:
            deps["notifier"] = notifier

        trips = TripService(
            deps["store"],
            deps["producer"],
            deps["notifier"],
            duration_s=env_float("TRIP_DURATION_S", 20),
            poll_s=env_float("COMPLETION_POLL_SECONDS", 1),
        )
        deps["trips"] = trips
        health.add_check("postgres", deps["store"].check)
        health.add_check("nats", deps["notifier"].check)

        worker = None
        if start_consumer:
            from .core.kafka import ConsumerWorker, readiness_check

            def handle(event, _msg):
                # The consumer thread hands each message to the event loop (one DB pool, one NATS
                # connection) and waits: an exception here means "retry this message".
                asyncio.run_coroutine_threadsafe(trips.on_ride_matched(event), loop).result(timeout=30)

            worker = ConsumerWorker(SERVICE, GROUP, IN_TOPIC, handle)
            health.add_blocking_check("kafka", readiness_check(SERVICE, [IN_TOPIC, COMPLETED_TOPIC]))
            health.add_check("consumer", worker.alive_check)
            worker.start()
        scheduler = asyncio.create_task(trips.run_scheduler())
        install_shutdown_hook(health, env_float("SHUTDOWN_DELAY_SECONDS", 0))
        log.info("started", extra={"group": GROUP, "topic": IN_TOPIC})
        try:
            yield
        finally:
            health.shutting_down = True
            if worker:
                await asyncio.to_thread(worker.stop)  # the worker may be waiting on this loop: don't block it
            trips.stop()
            await scheduler
            deps["producer"].close()
            await deps["notifier"].close()
            await deps["store"].close()
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health
    app.state.deps = deps

    @app.get("/trips/{ride_id}")
    async def get_trip(ride_id: uuid.UUID):
        trip = await deps["store"].get(str(ride_id))
        if trip is None:
            raise HTTPException(404, "no such trip")
        return trip

    if setup_tracing(SERVICE):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
        PsycopgInstrumentor().instrument()
    return app


app = create_app()
