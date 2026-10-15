import pytest

from app import pricing


def test_distance_berlin_mitte_to_kreuzberg():
    # Alexanderplatz → Kottbusser Tor is about 2.6 km
    assert pricing.haversine_km(52.5219, 13.4132, 52.4991, 13.4180) == pytest.approx(2.56, abs=0.05)


def test_distance_is_zero_for_the_same_point():
    assert pricing.haversine_km(52.5, 13.4, 52.5, 13.4) == 0


@pytest.mark.parametrize(
    ("demand", "supply", "expected"),
    [
        (0, 0, 1.0),  # nobody asking
        (3, 10, 1.0),  # more drivers than requests
        (10, 10, 1.0),  # balanced
        (20, 10, 1.2),  # twice the demand: +0.25, rounded to one decimal
        (50, 10, 2.0),
        (1000, 1, 3.0),  # capped
        (5, 0, 2.0),  # no drivers counts as one
    ],
)
def test_surge(demand, supply, expected):
    assert pricing.surge_multiplier(demand, supply) == expected


def test_fare_has_a_minimum():
    assert pricing.fare_cents(0.1, 1.0) == pricing.MINIMUM_FARE_CENTS


def test_fare_scales_with_distance_and_surge():
    assert pricing.fare_cents(10, 1.0) == 250 + 1200
    assert pricing.fare_cents(10, 2.0) == 2 * (250 + 1200)


def test_cell_is_stable_inside_a_cell():
    assert pricing.cell_of(52.52, 13.40) == pricing.cell_of(52.53, 13.41) == "cell:1050:268"
    assert pricing.cell_of(52.52, 13.40) != pricing.cell_of(52.56, 13.40)


@pytest.mark.parametrize(("lat", "lon", "ok"), [(52.5, 13.4, True), (91, 0, False), (0, -181, False)])
def test_valid_point(lat, lon, ok):
    assert pricing.valid_point(lat, lon) is ok
