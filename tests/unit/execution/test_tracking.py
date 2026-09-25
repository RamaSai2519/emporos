"""EM-217: order calls in flight are visible to a read-only consumer and change nothing else."""

from __future__ import annotations

import asyncio

import pytest

from emporos.broker.models import BrokerOrder, BrokerOrderAck, CancelOrderRequest, PlaceOrderRequest
from emporos.execution.tracking import InFlightOrders, TrackedOrderGateway


class Gateway:
    """Holds each call open until released, so the test can look while it is in flight."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.fail: Exception | None = None
        self.calls: list[str] = []

    async def _wait(self, name: str) -> None:
        self.calls.append(name)
        await self.release.wait()
        if self.fail:
            raise self.fail

    async def submit(self, request: PlaceOrderRequest) -> BrokerOrderAck:
        await self._wait("submit")
        return "ack"  # type: ignore[return-value]

    async def revoke(self, request: CancelOrderRequest) -> BrokerOrderAck:
        await self._wait("revoke")
        return "ack"  # type: ignore[return-value]

    async def find_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        await self._wait("find")
        return []


@pytest.mark.parametrize("call", ["submit", "revoke", "find_by_tag"])
async def test_an_order_call_is_pending_until_it_returns(call: str) -> None:
    inner, flight = Gateway(), InFlightOrders()
    gateway = TrackedOrderGateway(inner, flight)  # type: ignore[arg-type]

    task = asyncio.create_task(getattr(gateway, call)(None if call != "find_by_tag" else "tag"))
    await asyncio.sleep(0)
    assert flight.orders_pending() is True

    inner.release.set()
    await task
    assert flight.orders_pending() is False


async def test_a_failing_call_is_no_longer_pending_and_the_error_reaches_the_caller() -> None:
    inner, flight = Gateway(), InFlightOrders()
    inner.fail = RuntimeError("broker down")
    inner.release.set()
    gateway = TrackedOrderGateway(inner, flight)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="broker down"):
        await gateway.submit(None)  # type: ignore[arg-type]

    assert flight.orders_pending() is False


async def test_two_calls_in_flight_are_both_counted() -> None:
    inner, flight = Gateway(), InFlightOrders()
    gateway = TrackedOrderGateway(inner, flight)  # type: ignore[arg-type]
    a = asyncio.create_task(gateway.submit(None))  # type: ignore[arg-type]
    b = asyncio.create_task(gateway.revoke(None))  # type: ignore[arg-type]
    await asyncio.sleep(0)

    inner.release.set()
    await asyncio.gather(a, b)

    assert flight.orders_pending() is False and inner.calls == ["submit", "revoke"]
