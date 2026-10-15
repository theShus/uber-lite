"""The simulator against fake rider and driver APIs (httpx.MockTransport)."""

import argparse
import json

import httpx
import pytest

from app import simulate as sim


def fake_apis(ride_status=202):
    seen = {"rides": [], "pings": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/rides":
            seen["rides"].append(body)
            return httpx.Response(ride_status, json={"ride_id": "x"})
        if request.url.path.startswith("/drivers/") and request.url.path.endswith("/location"):
            seen["pings"].append(body)
            return httpx.Response(202, json={"status": "accepted"})
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


@pytest.mark.parametrize(("text", "seconds"), [("90", 90), ("90s", 90), ("2m", 120), ("1h", 3600), ("1m30s", 90)])
def test_parse_duration(text, seconds):
    assert sim.parse_duration(text) == seconds


@pytest.mark.parametrize("bad", ["2x", "m", "1m30"])
def test_bad_durations(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        sim.parse_duration(bad)


def test_movement_stays_in_the_box():
    lat, lon = 52.569, 13.499
    for _ in range(100):
        lat, lon = sim.move(lat, lon, heading=0.7, metres=500)  # north-east, into the edge
    lat_min, lat_max, lon_min, lon_max = sim.BERLIN
    assert lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def test_move_covers_the_distance():
    lat, lon = sim.move(52.50, 13.40, heading=0, metres=1113.2)  # due north
    assert lat == pytest.approx(52.51, abs=1e-4) and lon == pytest.approx(13.40)


async def test_a_short_run_sends_pings_and_rides():
    transport, seen = fake_apis()
    args = sim.parse_args(
        [
            "--drivers",
            "5",
            "--riders",
            "3",
            "--duration",
            "1",
            "--ping-interval",
            "0.1",
            "--ride-interval",
            "0.1",
            "--seed",
            "1",
        ]
    )
    result = await sim.simulate(args, "http://rider", "http://driver", transport=transport)
    assert result["pings"]["sent"] > 10 and result["pings"]["error_rate"] == 0
    assert result["rides"]["sent"] > 5 and result["rides"]["ok"] == result["rides"]["sent"]
    ride = seen["rides"][0]
    assert set(ride) == {"rider_id", "pickup", "dropoff"} and ride["rider_id"].startswith("r")


async def test_rush_hour_multiplies_the_riders():
    transport, seen = fake_apis()
    args = sim.parse_args(
        ["--drivers", "0", "--riders", "2", "--duration", "1", "--ride-interval", "0.05", "--rush-hour", "--seed", "1"]
    )
    await sim.simulate(args, "http://rider", "http://driver", transport=transport)
    assert {r["rider_id"] for r in seen["rides"]} == {f"r{i}" for i in range(10)}


async def test_errors_are_counted():
    transport, _ = fake_apis(ride_status=503)
    args = sim.parse_args(["--drivers", "0", "--riders", "3", "--duration", "0.5", "--ride-interval", "0.05"])
    result = await sim.simulate(args, "http://rider", "http://driver", transport=transport)
    assert result["rides"]["error_rate"] == 1.0
    assert set(result["rides"]["statuses"]) == {"503"}


def test_max_error_rate_turns_into_an_exit_code(monkeypatch):
    async def fake_simulate(args, *a, **k):
        return {"rides": {"error_rate": 0.2}, "pings": {}}

    monkeypatch.setattr(sim, "simulate", fake_simulate)
    assert sim.main(["--duration", "1", "--max-error-rate", "0.05"]) == 1
    assert sim.main(["--duration", "1", "--max-error-rate", "0.5"]) == 0
    assert sim.main(["--duration", "1"]) == 0
