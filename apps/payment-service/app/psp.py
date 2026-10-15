"""A fake payment service provider (PSP): sometimes slow, sometimes failing, on purpose.

PSP_LATENCY_MS     150    mean latency per charge (normally distributed, ±30%)
PSP_FAILURE_RATE   0.02   share of charges that fail

Those two knobs drive the SLO and alerting labs (topic 19): raise the failure rate,
and watch the error budget burn.
"""

import hashlib
import random
import time

from prometheus_client import Histogram

PSP_LATENCY = Histogram(
    "psp_request_duration_seconds",
    "Calls to the payment provider",
    ["result"],
    buckets=(0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1, 2.5),
)


class PSPError(Exception):
    pass


class FakePSP:
    def __init__(
        self, latency_ms: float = 150, failure_rate: float = 0.02, rng: random.Random | None = None, sleep=None
    ):
        self.latency_ms = latency_ms
        self.failure_rate = failure_rate
        self.rng = rng or random.Random()
        self.sleep = sleep or time.sleep

    def charge(self, idempotency_key: str, amount_cents: int, currency: str) -> str:
        """Charge once per idempotency key. Real providers (Stripe, Adyen) take such a key, so a retry
        after a timeout can't charge the customer twice; here the reference is derived from it."""
        delay = max(0.0, self.rng.gauss(self.latency_ms, self.latency_ms * 0.3)) / 1000
        self.sleep(delay)
        if self.rng.random() < self.failure_rate:
            PSP_LATENCY.labels("error").observe(delay)
            raise PSPError("provider error: card network timeout")
        PSP_LATENCY.labels("ok").observe(delay)
        return "psp_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
