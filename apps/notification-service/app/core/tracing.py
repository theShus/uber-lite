"""OpenTelemetry tracing, only when OTEL_EXPORTER_OTLP_ENDPOINT is set (topic 19).

Spans are exported with OTLP over HTTP (a collector's port 4318). Each service's
main.py instruments its HTTP server and clients, gRPC, Postgres and Valkey calls.
Context is NOT propagated through Kafka or NATS messages yet. Adding that is your
change in topic 19; until then a trace ends at the producer.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(service: str) -> bool:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", service)})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint from the env var
    trace.set_tracer_provider(provider)
    return True


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
