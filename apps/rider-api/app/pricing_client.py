"""The gRPC client for pricing-service.

PRICING_ADDR=pricing-service:50051
    one connection, through the ClusterIP: every call from this pod goes to ONE pricing pod (16.3)
PRICING_ADDR=dns:///pricing-service-headless.uber-lite.svc.cluster.local:50051
    gRPC resolves every pod IP behind the headless Service and round-robins calls across them
"""

import grpc
from prometheus_client import Counter

from .proto import pricing_pb2, pricing_pb2_grpc

PRICING_ERRORS = Counter("pricing_client_errors_total", "Failed calls to pricing-service", ["code"])


class PricingUnavailable(Exception):
    pass


def channel_options(target: str) -> list[tuple[str, str]]:
    """round_robin only makes sense when the name resolves to many addresses (a headless Service)."""
    return [("grpc.lb_policy_name", "round_robin")] if target.startswith("dns:///") else []


class PricingClient:
    def __init__(self, target: str, timeout: float = 2.0):
        self.target = target
        self.timeout = timeout
        self.channel = grpc.aio.insecure_channel(target, options=channel_options(target))
        self.stub = pricing_pb2_grpc.PricingStub(self.channel)

    async def quote(self, rider_id: str, pickup: dict, dropoff: dict) -> pricing_pb2.QuoteReply:
        request = pricing_pb2.QuoteRequest(
            rider_id=rider_id, pickup=pricing_pb2.Point(**pickup), dropoff=pricing_pb2.Point(**dropoff)
        )
        try:
            return await self.stub.Quote(request, timeout=self.timeout)
        except grpc.aio.AioRpcError as exc:
            PRICING_ERRORS.labels(exc.code().name).inc()
            raise PricingUnavailable(f"{exc.code().name}: {exc.details()}") from exc

    async def close(self) -> None:
        await self.channel.close()
