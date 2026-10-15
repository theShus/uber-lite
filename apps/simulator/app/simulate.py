"""The load generator: drivers driving around Berlin, riders requesting rides.

python -m app.simulate --drivers 50 --riders 10 --duration 2m
python -m app.simulate --drivers 300 --riders 30 --duration 5m --rush-hour

Environment: RIDER_API (default http://rider-api), DRIVER_API (default http://driver-api),
RIDER_API_KEY (optional: sent as the apikey header, for going through Kong's key-auth).
"""

import argparse
import asyncio
import contextlib
import math
import os
import random
import re
import signal
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx

from .core.logging import setup_logging

log = setup_logging("simulator")

BERLIN = (52.45, 52.57, 13.28, 13.50)  # lat min, lat max, lon min, lon max
RUSH_HOUR_RIDER_FACTOR = 5
PROGRESS_EVERY_S = 30


def parse_duration(text: str) -> float:
    """'90s', '2m', '1h', '1m30s' or plain seconds → seconds."""
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = re.findall(r"(\d+)([hms])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise argparse.ArgumentTypeError(f"not a duration: {text!r} (try 90s, 2m, 1h)")
    return float(sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in parts))


def clamp_to_box(lat: float, lon: float) -> tuple[float, float]:
    lat_min, lat_max, lon_min, lon_max = BERLIN
    return min(max(lat, lat_min), lat_max), min(max(lon, lon_min), lon_max)


def random_point(rng: random.Random) -> tuple[float, float]:
    lat_min, lat_max, lon_min, lon_max = BERLIN
    return rng.uniform(lat_min, lat_max), rng.uniform(lon_min, lon_max)


def move(lat: float, lon: float, heading: float, metres: float) -> tuple[float, float]:
    dlat = metres * math.cos(heading) / 111_320
    dlon = metres * math.sin(heading) / (111_320 * math.cos(math.radians(lat)))
    return clamp_to_box(lat + dlat, lon + dlon)


@dataclass
class Stats:
    statuses: Counter = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)

    def record(self, status: str, seconds: float) -> None:
        self.statuses[status] += 1
        self.latencies_ms.append(seconds * 1000)

    def summary(self) -> dict:
        total = sum(self.statuses.values())
        ok = sum(n for s, n in self.statuses.items() if s.startswith("2"))
        lat = sorted(self.latencies_ms)
        return {
            "sent": total,
            "ok": ok,
            "error_rate": round((total - ok) / total, 4) if total else 0.0,
            "statuses": dict(self.statuses),
            "p50_ms": round(statistics.median(lat), 1) if lat else None,
            "p95_ms": round(lat[int(len(lat) * 0.95) - 1], 1) if len(lat) >= 20 else None,
        }


async def timed_post(client: httpx.AsyncClient, url: str, payload: dict, stats: Stats, headers=None) -> None:
    started = time.perf_counter()
    try:
        response = await client.post(url, json=payload, headers=headers)
        stats.record(str(response.status_code), time.perf_counter() - started)
    except httpx.HTTPError as exc:
        stats.record(type(exc).__name__, time.perf_counter() - started)


async def drive(client, base: str, driver_id: str, interval: float, stop: asyncio.Event, stats: Stats, rng):
    lat, lon = random_point(rng)
    heading = rng.uniform(0, 2 * math.pi)
    await asyncio.sleep(rng.uniform(0, interval))  # spread the drivers out
    while not stop.is_set():
        heading += rng.gauss(0, 0.4)  # wander
        lat, lon = move(lat, lon, heading, rng.uniform(8, 14) * interval)  # 8–14 m/s
        await timed_post(client, f"{base}/drivers/{driver_id}/location", {"lat": lat, "lon": lon}, stats)
        await asyncio.sleep(interval * rng.uniform(0.8, 1.2))


async def request_rides(client, base: str, rider_id: str, interval: float, stop, stats: Stats, rng, headers):
    while not stop.is_set():
        await asyncio.sleep(rng.expovariate(1 / interval))  # Poisson arrivals: bursty, like real demand
        if stop.is_set():
            break
        pickup = random_point(rng)
        dropoff = move(*pickup, rng.uniform(0, 2 * math.pi), rng.uniform(1000, 8000))
        body = {
            "rider_id": rider_id,
            "pickup": {"lat": pickup[0], "lon": pickup[1]},
            "dropoff": {"lat": dropoff[0], "lon": dropoff[1]},
        }
        await timed_post(client, f"{base}/rides", body, stats, headers)


async def simulate(args, rider_api: str, driver_api: str, api_key: str | None = None, transport=None) -> dict:
    rng = random.Random(args.seed)
    riders = args.riders * (RUSH_HOUR_RIDER_FACTOR if args.rush_hour else 1)
    headers = {"apikey": api_key} if api_key else None
    pings, rides = Stats(), Stats()
    stop = asyncio.Event()
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=100)
    log.info(
        "simulation starting",
        extra={"drivers": args.drivers, "riders": riders, "duration_s": args.duration, "rush_hour": args.rush_hour},
    )
    async with httpx.AsyncClient(timeout=5, limits=limits, transport=transport) as client:
        tasks = [
            asyncio.create_task(drive(client, driver_api, f"d{i}", args.ping_interval, stop, pings, rng))
            for i in range(args.drivers)
        ] + [
            asyncio.create_task(
                request_rides(client, rider_api, f"r{i}", args.ride_interval, stop, rides, rng, headers)
            )
            for i in range(riders)
        ]
        started = time.monotonic()
        try:
            while (elapsed := time.monotonic() - started) < args.duration:
                await asyncio.sleep(min(PROGRESS_EVERY_S, args.duration - elapsed))
                if time.monotonic() - started < args.duration:
                    log.info(
                        "progress",
                        extra={"elapsed_s": round(elapsed), "rides": rides.summary(), "pings": pings.summary()},
                    )
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    result = {"rides": rides.summary(), "pings": pings.summary()}
    log.info("simulation finished", extra=result)
    return result


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m app.simulate", description=__doc__.split("\n")[0])
    p.add_argument("--drivers", type=int, default=50, help="simulated drivers sending locations (default 50)")
    p.add_argument("--riders", type=int, default=10, help="simulated riders requesting rides (default 10)")
    p.add_argument("--duration", type=parse_duration, default=parse_duration("2m"), help="e.g. 90s, 5m (default 2m)")
    p.add_argument("--rush-hour", action="store_true", help=f"{RUSH_HOUR_RIDER_FACTOR}× the riders")
    p.add_argument("--ping-interval", type=float, default=2.0, help="seconds between a driver's pings (default 2)")
    p.add_argument("--ride-interval", type=float, default=30.0, help="mean seconds between a rider's requests (30)")
    p.add_argument(
        "--max-error-rate",
        type=float,
        default=None,
        help="exit 1 if the share of failed ride requests is above this (a smoke test)",
    )
    p.add_argument("--seed", type=int, default=None, help="random seed, for repeatable runs")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    rider_api = os.environ.get("RIDER_API", "http://rider-api")
    driver_api = os.environ.get("DRIVER_API", "http://driver-api")

    async def run() -> dict:
        task = asyncio.create_task(simulate(args, rider_api, driver_api, os.environ.get("RIDER_API_KEY")))
        with contextlib.suppress(NotImplementedError, AttributeError):  # no signal handlers on Windows
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        return await task

    try:
        result = asyncio.run(run())
    except asyncio.CancelledError:
        log.info("stopped by SIGTERM")
        return 0
    if args.max_error_rate is not None and result["rides"]["error_rate"] > args.max_error_rate:
        log.error("ride error rate above the limit", extra={"limit": args.max_error_rate, "rides": result["rides"]})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
