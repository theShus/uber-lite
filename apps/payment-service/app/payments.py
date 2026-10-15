"""Charging: trip.completed → one payment per ride, whatever Kafka redelivers."""

import datetime
import logging
import time
import uuid

from prometheus_client import Counter
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .core.kafka import PoisonMessage
from .psp import PSPError

SETTLED_TOPIC = "payment.settled"

log = logging.getLogger("payments")

PAYMENTS = Counter("payments_total", "Payments by outcome", ["result"])  # charged, failed, duplicate


class PgPaymentStore:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def check(self) -> None:
        with self.pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")

    def begin(self, ride_id: str, amount_cents: int, currency: str) -> dict:
        """Create the payment as 'pending' if it's new, and return its current row either way."""
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO payments (ride_id, amount_cents, currency, status) VALUES (%s, %s, %s, 'pending')"
                " ON CONFLICT (ride_id) DO NOTHING",
                (ride_id, amount_cents, currency),
            )
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                "SELECT ride_id::text AS ride_id, amount_cents, currency, status, attempts, psp_reference"
                " FROM payments WHERE ride_id = %s",
                (ride_id,),
            )
            return cur.fetchone()

    def record_attempt(self, ride_id: str) -> int:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE payments SET attempts = attempts + 1, updated_at = now() WHERE ride_id = %s RETURNING attempts",
                (ride_id,),
            )
            return cur.fetchone()[0]

    def finish(self, ride_id: str, status: str, psp_reference: str | None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE payments SET status = %s, psp_reference = %s, updated_at = now() WHERE ride_id = %s",
                (status, psp_reference, ride_id),
            )

    def close(self) -> None:
        self.pool.close()


class PaymentProcessor:
    def __init__(self, store, producer, psp, max_attempts: int = 3, backoff_s: float = 0.2, sleep=time.sleep):
        self.store = store
        self.producer = producer
        self.psp = psp
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.sleep = sleep

    def handle(self, event: dict, _msg) -> None:
        try:
            ride_id = str(uuid.UUID(str(event["ride_id"])))
            amount_cents = int(event["price"]["amount_cents"])
            currency = str(event["price"]["currency"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PoisonMessage(f"bad trip.completed event: {exc}") from exc

        payment = self.store.begin(ride_id, amount_cents, currency)  # a DB error raises: the message is retried
        if payment["status"] in ("charged", "failed"):
            # A redelivered trip.completed: never charge twice. Re-send the settlement instead.
            PAYMENTS.labels("duplicate").inc()
            log.info(
                "duplicate trip.completed, already settled", extra={"ride_id": ride_id, "status": payment["status"]}
            )
            self.publish(payment)
            return
        # 'pending' is a new payment, or one interrupted by a crash: charging again is safe,
        # because the provider deduplicates on the idempotency key.
        for attempt in range(1, self.max_attempts + 1):
            self.store.record_attempt(ride_id)
            try:
                reference = self.psp.charge(ride_id, amount_cents, currency)
            except PSPError as exc:
                if attempt < self.max_attempts:
                    backoff = self.backoff_s * 2 ** (attempt - 1)
                    log.warning(
                        "charge failed, retrying",
                        extra={"ride_id": ride_id, "attempt": attempt, "backoff_s": backoff, "error": str(exc)},
                    )
                    self.sleep(backoff)
                    continue
                self.store.finish(ride_id, "failed", None)
                PAYMENTS.labels("failed").inc()
                log.error("payment failed", extra={"ride_id": ride_id, "attempts": attempt, "error": str(exc)})
                self.publish(payment | {"status": "failed", "attempts": attempt})
                return
            self.store.finish(ride_id, "charged", reference)
            PAYMENTS.labels("charged").inc()
            log.info("charged", extra={"ride_id": ride_id, "amount_cents": amount_cents, "attempts": attempt})
            self.publish(payment | {"status": "charged", "psp_reference": reference, "attempts": attempt})
            return

    def publish(self, payment: dict) -> None:
        self.producer.send_sync(
            SETTLED_TOPIC,
            payment["ride_id"],
            {
                "ride_id": payment["ride_id"],
                "status": payment["status"],
                "amount_cents": payment["amount_cents"],
                "currency": payment["currency"],
                "psp_reference": payment.get("psp_reference"),
                "attempts": payment.get("attempts"),
                "settled_at": datetime.datetime.now(datetime.UTC).isoformat(),
            },
        )
