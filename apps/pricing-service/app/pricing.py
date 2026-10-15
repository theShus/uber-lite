"""Pricing rules: distance, surge, fare. Pure functions, so they're easy to test."""

import math

BASE_FARE_CENTS = 250
PER_KM_CENTS = 120
MINIMUM_FARE_CENTS = 500
CURRENCY = "EUR"

MAX_SURGE = 3.0
CELL_DEGREES = 0.05  # demand is counted per grid cell of 0.05° × 0.05° (about 5.5 km × 3.4 km in Berlin)
SUPPLY_RADIUS_KM = 3.0
DEMAND_WINDOW_SECONDS = 300


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cell_of(lat: float, lon: float) -> str:
    """The demand cell a point falls into, e.g. 'cell:1050:268' for Berlin Mitte."""
    return f"cell:{math.floor(lat / CELL_DEGREES)}:{math.floor(lon / CELL_DEGREES)}"


def surge_multiplier(demand: int, supply: int) -> float:
    """1.0 while drivers outnumber requests; +0.25 per extra request per driver, capped at 3.0, one decimal."""
    ratio = demand / max(supply, 1)
    if ratio <= 1:
        return 1.0
    return round(min(MAX_SURGE, 1 + (ratio - 1) * 0.25), 1)


def fare_cents(distance_km: float, surge: float) -> int:
    return max(MINIMUM_FARE_CENTS, round((BASE_FARE_CENTS + PER_KM_CENTS * distance_km) * surge))


def valid_point(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180
