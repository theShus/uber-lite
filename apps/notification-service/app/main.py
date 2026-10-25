"""notification-service: "sends" push notifications (it logs them).

Kafka notification.requested (group notification-service), keyed <kind>:<id>, published by trip-service:
  {"kind": "rider" | "driver", "target": "<id>", "ride_id": "...", "message": "..."}  → one log line per push

Scales to zero with KEDA when the group has no lag (topic 21).
Until 2026-10-25 this read NATS JetStream; docs/app-reference.md (History) says why it changed.

Run: uvicorn app.main:app --host 0.0.0.0 --port 8080   (HTTP only serves health and metrics)
"""

import contextlib

from fastapi import FastAPI
from prometheus_client import Counter

from .core.config import env_float
from .core.health import Health, health_router
from .core.kafka import PoisonMessage
from .core.lifecycle import install_shutdown_hook
from .core.logging import setup_logging
from .core.metrics import RequestMetricsMiddleware
from .core.tracing import FASTAPI_TELEMETRY, setup_tracing, shutdown_tracing

SERVICE = "notification-service"
TOPIC = "notification.requested"
GROUP = "notification-service"
KINDS = ("rider", "driver")
log = setup_logging(SERVICE)

SENT = Counter("notifications_sent_total", "Push notifications 'sent'", ["kind"])


def handle(event: dict, _msg) -> None:
    """One notification: 'send' the push (a log line). A malformed event is poison: logged, counted, skipped.

    A real push provider would be called here. A redelivered message (at-least-once) means the rider may see
    the same push twice; making that impossible would need the provider's idempotency key, like payment-service.
    """
    try:
        kind, target, message = str(event["kind"]), str(event["target"]), str(event["message"])
    except KeyError as exc:
        raise PoisonMessage(f"bad notification event: missing {exc}") from exc
    if kind not in KINDS:
        raise PoisonMessage(f"unknown recipient kind {kind!r}")
    log.info(
        f"sending push to {kind} {target}: {message}",
        extra={"kind": kind, "target": target, "ride_id": event.get("ride_id")},
    )
    SENT.labels(kind).inc()


def create_app(start_consumer: bool = True) -> FastAPI:
    """Tests pass start_consumer=False, and call handle() directly."""
    health = Health()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if start_consumer:
            from .core.kafka import ConsumerWorker, readiness_check

            worker = ConsumerWorker(SERVICE, GROUP, TOPIC, handle)
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
            shutdown_tracing()
            log.info("stopped")

    app = FastAPI(title=SERVICE, lifespan=lifespan, telemetry=FASTAPI_TELEMETRY)
    app.add_middleware(RequestMetricsMiddleware)
    app.include_router(health_router(health))
    app.state.health = health
    setup_tracing(SERVICE)
    return app


app = create_app()
