"""Matching: the nearest online, free driver for each ride.

Valkey keys (location-service writes the first two):
  drivers:geo                       GEO set of driver positions
  driver:last_seen:<driver_id>      exists while the driver is online
  busy:<driver_id> = <ride_id>      claimed with SET NX: two replicas can't take the same driver
  matched:<ride_id> = <driver_id>   makes a redelivered ride.requested produce the SAME match
"""

import datetime
import logging

import redis
from prometheus_client import Counter

from .core.kafka import PoisonMessage

GEO_KEY = "drivers:geo"
SEARCH_RADII_KM = (3, 10)
CANDIDATES = 20
OUT_TOPIC = "ride.matched"

log = logging.getLogger("matcher")

MATCHED = Counter("rides_matched_total", "Rides matched to a driver")
UNMATCHED = Counter("rides_unmatched_total", "Rides with no free driver within 10 km")
REDELIVERED = Counter("rides_rematched_total", "Redelivered rides answered with their existing match")


class Matcher:
    def __init__(self, valkey: redis.Redis, producer, busy_ttl: int = 60, matched_ttl: int = 3600):
        self.valkey = valkey
        self.producer = producer
        self.busy_ttl = busy_ttl
        self.matched_ttl = matched_ttl

    def handle(self, event: dict, _msg) -> None:
        try:
            ride_id, rider_id = str(event["ride_id"]), str(event["rider_id"])
            lat, lon = float(event["pickup"]["lat"]), float(event["pickup"]["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PoisonMessage(f"bad ride.requested event: {exc}") from exc

        existing = self.valkey.get(f"matched:{ride_id}")
        if existing is not None:
            # At-least-once delivery: we've seen this ride. Send the same match again; trip-service ignores duplicates.
            driver_id, distance = existing.decode(), None
            REDELIVERED.inc()
        else:
            found = self.find_driver(ride_id, lat, lon)
            if found is None:
                UNMATCHED.inc()
                log.warning("no driver available", extra={"ride_id": ride_id, "rider_id": rider_id})
                return
            driver_id, distance = found
            self.valkey.set(f"matched:{ride_id}", driver_id, ex=self.matched_ttl)

        matched = {
            "ride_id": ride_id,
            "rider_id": rider_id,
            "driver_id": driver_id,
            "distance_km": distance,
            "pickup": event["pickup"],
            "dropoff": event.get("dropoff"),
            "price": event.get("price"),
            "matched_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        self.producer.send_sync(OUT_TOPIC, ride_id, matched)  # acknowledged before our offset is stored
        MATCHED.inc()
        log.info("ride matched", extra={"ride_id": ride_id, "driver_id": driver_id, "distance_km": distance})

    def find_driver(self, ride_id: str, lat: float, lon: float) -> tuple[str, float] | None:
        for radius in SEARCH_RADII_KM:
            candidates = self.valkey.geosearch(
                GEO_KEY,
                longitude=lon,
                latitude=lat,
                radius=radius,
                unit="km",
                sort="ASC",
                count=CANDIDATES,
                withdist=True,
            )
            for member, distance in candidates:
                driver_id = member.decode()
                if not self.valkey.exists(f"driver:last_seen:{driver_id}"):
                    self.valkey.zrem(GEO_KEY, driver_id)  # stopped pinging: offline, tidy the index
                    continue
                if self.valkey.set(f"busy:{driver_id}", ride_id, nx=True, ex=self.busy_ttl):
                    return driver_id, round(distance, 3)
        return None
