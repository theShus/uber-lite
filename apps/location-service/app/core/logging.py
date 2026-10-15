"""JSON log lines on stdout: ts, level, service, msg, plus any `extra` fields."""

import datetime
import json
import logging
import os
import sys

from opentelemetry import trace

# Attributes every LogRecord has; anything else came from `extra=` and is logged as a field.
# (uvicorn adds color_message, a copy of msg with terminal colour codes.)
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.datetime.fromtimestamp(record.created, tz=datetime.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = value
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            entry["trace_id"] = format(span.trace_id, "032x")
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(service: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    # uvicorn's own loggers go through the JSON handler. Its access log is replaced by
    # our request line (metrics.py), which knows the route template and skips probes.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "nats"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger(service)
