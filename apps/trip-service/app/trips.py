"""Trip logic: start a trip per ride.matched, complete it when its (simulated) drive ends."""

import asyncio
import contextlib
import datetime
import logging
import uuid

from prometheus_client import Counter

from .core.kafka import PoisonMessage

COMPLETED_TOPIC = "trip.completed"

log = logging.getLogger("trips")

TRIPS_STARTED = Counter("trips_started_total", "Trips started")
TRIPS_COMPLETED = Counter("trips_completed_total", "Trips completed (trip.completed published)")
DUPLICATES = Counter("trips_duplicate_matches_total", "ride.matched events for trips that already exist")
NOTIFY_FAILURES = Counter("trip_notifications_failed_total", "Notifications that couldn't be published")


class TripService:
    def __init__(self, store, producer, notifier, duration_s: float = 20.0, poll_s: float = 1.0):
        self.store = store
        self.producer = producer
        self.notifier = notifier
        self.duration_s = duration_s
        self.poll_s = poll_s
        self._stop = asyncio.Event()

    # ---- ride.matched → a started trip (called on the event loop, from the consumer thread) ----
    async def on_ride_matched(self, event: dict) -> None:
        try:
            trip = {
                "ride_id": str(uuid.UUID(str(event["ride_id"]))),
                "rider_id": str(event["rider_id"]),
                "driver_id": str(event["driver_id"]),
                "amount_cents": int(event["price"]["amount_cents"]),
                "currency": str(event["price"]["currency"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise PoisonMessage(f"bad ride.matched event: {exc}") from exc
        created = await self.store.start(trip, self.duration_s)  # a DB error raises: the message is retried
        if not created:
            DUPLICATES.inc()
            log.info("duplicate ride.matched ignored", extra={"ride_id": trip["ride_id"]})
            return
        TRIPS_STARTED.inc()
        log.info("trip started", extra={"ride_id": trip["ride_id"], "driver_id": trip["driver_id"]})
        await self.notify(f"notify.rider.{trip['rider_id']}", trip["ride_id"], "Your driver is on the way")
        await self.notify(f"notify.driver.{trip['driver_id']}", trip["ride_id"], "New ride: head to the pickup")

    async def notify(self, subject: str, ride_id: str, message: str) -> None:
        """Best effort: a lost notification mustn't stall the trip pipeline."""
        try:
            await self.notifier.publish(subject, {"ride_id": ride_id, "message": message})
        except Exception as exc:
            NOTIFY_FAILURES.inc()
            log.warning("notification not published", extra={"subject": subject, "ride_id": ride_id, "error": str(exc)})

    # ---- the completion scheduler ----
    async def run_scheduler(self) -> None:
        while not self._stop.is_set():
            try:
                while await self.complete_batch():  # drain everything that's due
                    pass
            except Exception as exc:
                log.warning("completing trips failed, will retry", extra={"error": f"{type(exc).__name__}: {exc}"})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.poll_s)

    async def complete_batch(self) -> int:
        async def publish(trips: list[dict]) -> None:
            now = datetime.datetime.now(datetime.UTC).isoformat()
            await asyncio.gather(
                *(
                    self.producer.send(
                        COMPLETED_TOPIC,
                        t["ride_id"],
                        {
                            "ride_id": t["ride_id"],
                            "rider_id": t["rider_id"],
                            "driver_id": t["driver_id"],
                            "price": {"amount_cents": t["amount_cents"], "currency": t["currency"]},
                            "completed_at": now,
                        },
                    )
                    for t in trips
                )
            )

        done = await self.store.complete_due(publish)
        for t in done:
            TRIPS_COMPLETED.inc()
            log.info("trip completed", extra={"ride_id": t["ride_id"], "amount_cents": t["amount_cents"]})
            euros = f"{t['amount_cents'] / 100:.2f} {t['currency']}"
            await self.notify(f"notify.rider.{t['rider_id']}", t["ride_id"], f"Trip completed: {euros}")
        return len(done)

    def stop(self) -> None:
        self._stop.set()
