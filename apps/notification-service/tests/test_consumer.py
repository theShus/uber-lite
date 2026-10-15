"""Message handling (fake messages) and, when NATS_TEST_URL points at a nats-server -js, the real thing."""

import asyncio
import json
import os

import httpx
import pytest

from app.consumer import DURABLE, STREAM, NotificationConsumer, parse_subject, process
from app.main import create_app


class FakeMsg:
    def __init__(self, subject, data):
        self.subject = subject
        self.data = data
        self.acked = self.terminated = False

    async def ack(self):
        self.acked = True

    async def term(self):
        self.terminated = True


def test_parse_subject():
    assert parse_subject("notify.rider.r1") == ("rider", "r1")
    with pytest.raises(ValueError):
        parse_subject("notify.rider")


async def test_a_notification_is_sent_and_acked(caplog):
    msg = FakeMsg("notify.rider.r1", json.dumps({"ride_id": "x", "message": "Your driver is on the way"}).encode())
    with caplog.at_level("INFO"):
        await process(msg)
    assert msg.acked and not msg.terminated
    assert "sending push to rider r1: Your driver is on the way" in caplog.text


@pytest.mark.parametrize(
    ("subject", "data"),
    [("notify.rider.r1", b"not json"), ("notify.rider.r1", b'{"ride_id": "x"}'), ("other", b'{"message": "hi"}')],
)
async def test_bad_messages_are_terminated(subject, data):
    msg = FakeMsg(subject, data)
    await process(msg)
    assert msg.terminated and not msg.acked


async def test_readiness_is_503_until_consuming():
    app = create_app(start_consumer=False)
    app.state.health.add_check("nats", NotificationConsumer("nats://nowhere:4222", "t").check)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://n") as http:
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.get("/readyz")).status_code == 503


@pytest.mark.skipif(not os.environ.get("NATS_TEST_URL"), reason="set NATS_TEST_URL to a nats-server with JetStream")
async def test_against_a_real_nats_server(caplog):
    import nats

    url = os.environ["NATS_TEST_URL"]
    consumer = NotificationConsumer(url, "test")
    task = asyncio.create_task(consumer.run())
    try:
        for _ in range(50):
            if consumer.sub is not None:
                break
            await asyncio.sleep(0.1)
        await consumer.check()
        nc = await nats.connect(url)
        js = nc.jetstream()
        with caplog.at_level("INFO"):
            await js.publish("notify.rider.it", json.dumps({"ride_id": "it", "message": "hello"}).encode())
            for _ in range(100):
                if "sending push to rider it: hello" in caplog.text:
                    break
                await asyncio.sleep(0.1)
        assert "sending push to rider it: hello" in caplog.text
        info = await js.consumer_info(STREAM, DURABLE)
        assert info.num_ack_pending == 0
        await nc.close()
    finally:
        await consumer.stop()
        task.cancel()
