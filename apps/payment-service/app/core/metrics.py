"""Prometheus metrics, and the one log line per request."""

import logging
import time

from prometheus_client import Counter, Histogram
from starlette.routing import Match

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests handled", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
KAFKA_PRODUCED = Counter("kafka_messages_produced_total", "Kafka messages acknowledged by the broker", ["topic"])
KAFKA_PRODUCE_ERRORS = Counter("kafka_produce_errors_total", "Kafka messages that failed to be produced", ["topic"])
KAFKA_CONSUMED = Counter(
    "kafka_messages_consumed_total", "Kafka messages handled, by result (ok, retry, dropped)", ["topic", "result"]
)
KAFKA_PROCESSING = Histogram(
    "kafka_message_processing_seconds",
    "Time to process one Kafka message",
    ["topic"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# Probes and scrapes would drown the logs and skew the request metrics.
QUIET_PATHS = {"/healthz", "/readyz", "/metrics"}

log = logging.getLogger("http")


def route_template(app, scope) -> str:
    """The matched route's template (/rides/{ride_id}), never the raw path: raw paths explode label cardinality."""
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "path", "unmatched")
    return "unmatched"


class RequestMetricsMiddleware:
    """Pure ASGI middleware: counts and times every request, and logs one INFO line per request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in QUIET_PATHS:
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = route_template(scope["app"], scope) if "app" in scope else "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status["code"])).inc()
            HTTP_DURATION.labels(method, route).observe(elapsed)
            log.info(
                "request",
                extra={
                    "method": method,
                    "path": scope["path"],
                    "route": route,
                    "status": status["code"],
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
