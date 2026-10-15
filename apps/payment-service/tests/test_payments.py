"""Payment rules, with an in-memory store, a scripted provider and a fake producer."""

import random
import uuid

import httpx
import pytest

from app.core.kafka import PoisonMessage
from app.main import create_app
from app.payments import PaymentProcessor
from app.psp import FakePSP, PSPError


def completed(ride_id=None, cents=736):
    return {"ride_id": ride_id or str(uuid.uuid4()), "price": {"amount_cents": cents, "currency": "EUR"}}


class FakeStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.down = False

    def check(self):
        if self.down:
            raise ConnectionError("postgres is down")

    def begin(self, ride_id, amount_cents, currency):
        self.check()
        self.rows.setdefault(
            ride_id,
            {
                "ride_id": ride_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "status": "pending",
                "attempts": 0,
                "psp_reference": None,
            },
        )
        return dict(self.rows[ride_id])

    def record_attempt(self, ride_id):
        self.rows[ride_id]["attempts"] += 1
        return self.rows[ride_id]["attempts"]

    def finish(self, ride_id, status, psp_reference):
        self.rows[ride_id] |= {"status": status, "psp_reference": psp_reference}

    def close(self):
        pass


class ScriptedPSP:
    """Fails the first `failures` calls, then succeeds."""

    def __init__(self, failures=0):
        self.failures = failures
        self.calls = 0

    def charge(self, key, amount_cents, currency):
        self.calls += 1
        if self.calls <= self.failures:
            raise PSPError("provider error")
        return f"psp_{key[:8]}"


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send_sync(self, topic, key, value):
        self.sent.append((topic, key, value))

    def close(self):
        pass


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def producer():
    return FakeProducer()


def processor(store, producer, psp):
    return PaymentProcessor(store, producer, psp, max_attempts=3, backoff_s=0, sleep=lambda s: None)


def test_a_completed_trip_is_charged_once(store, producer):
    psp = ScriptedPSP()
    event = completed()
    processor(store, producer, psp).handle(event, None)
    row = store.rows[event["ride_id"]]
    assert row["status"] == "charged" and row["attempts"] == 1
    topic, key, settled = producer.sent[0]
    assert (topic, key) == ("payment.settled", event["ride_id"])
    assert settled["status"] == "charged" and settled["amount_cents"] == 736 and settled["psp_reference"]


def test_a_redelivered_trip_is_never_charged_twice(store, producer):
    psp = ScriptedPSP()
    p = processor(store, producer, psp)
    event = completed()
    p.handle(event, None)
    p.handle(event, None)  # Kafka delivered it again
    assert psp.calls == 1
    assert [e["status"] for _, _, e in producer.sent] == ["charged", "charged"]  # the settlement is re-sent


def test_provider_errors_are_retried(store, producer):
    psp = ScriptedPSP(failures=2)
    event = completed()
    processor(store, producer, psp).handle(event, None)
    assert psp.calls == 3
    assert store.rows[event["ride_id"]]["status"] == "charged"
    assert store.rows[event["ride_id"]]["attempts"] == 3


def test_three_failures_mark_the_payment_failed(store, producer):
    psp = ScriptedPSP(failures=5)
    event = completed()
    processor(store, producer, psp).handle(event, None)
    assert psp.calls == 3
    assert store.rows[event["ride_id"]]["status"] == "failed"
    assert producer.sent[0][2]["status"] == "failed"


def test_a_pending_payment_from_a_crash_is_finished(store, producer):
    event = completed()
    store.begin(event["ride_id"], 736, "EUR")  # left 'pending' by a crashed pod
    processor(store, producer, ScriptedPSP()).handle(event, None)
    assert store.rows[event["ride_id"]]["status"] == "charged"


def test_database_errors_raise_for_a_retry(store, producer):
    store.down = True
    with pytest.raises(ConnectionError):
        processor(store, producer, ScriptedPSP()).handle(completed(), None)


@pytest.mark.parametrize(
    "bad", [{"ride_id": "nope", "price": {"amount_cents": 1, "currency": "EUR"}}, {"ride_id": str(uuid.uuid4())}]
)
def test_bad_events_are_poison(store, producer, bad):
    with pytest.raises(PoisonMessage):
        processor(store, producer, ScriptedPSP()).handle(bad, None)


def test_the_fake_psp_is_deterministic_per_key_and_fails_at_its_rate():
    psp = FakePSP(latency_ms=0, failure_rate=0.5, rng=random.Random(42), sleep=lambda s: None)
    outcomes = []
    for _ in range(1000):
        try:
            outcomes.append(psp.charge("ride-1", 100, "EUR"))
        except PSPError:
            outcomes.append(None)
    failures = outcomes.count(None)
    assert 400 < failures < 600
    assert len({o for o in outcomes if o}) == 1  # the same key always gives the same reference


async def test_http_endpoints(store, producer):
    app = create_app(store=store, producer=producer, start_consumer=False)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://p") as http:
        assert (await http.get("/readyz")).json()["checks"] == {"postgres": "ok"}
        body = (await http.get("/metrics")).text
        assert "psp_request_duration_seconds" in body
