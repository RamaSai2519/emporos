"""Which order calls are on their way to the broker right now (EM-217).

`TrackedOrderGateway` wraps any `OrderGateway` and changes nothing about it: it counts the calls
that are in flight, so a read-only consumer of the same session (the quote recorder) can ask
`orders_pending()` and stand aside. Orders always go first."""

from __future__ import annotations

from emporos.broker.models import BrokerOrder, BrokerOrderAck, CancelOrderRequest, PlaceOrderRequest
from emporos.execution.ports import OrderGateway

__all__ = ["InFlightOrders", "TrackedOrderGateway"]


class InFlightOrders:
    def __init__(self) -> None:
        self._count = 0

    def orders_pending(self) -> bool:
        return self._count > 0

    def entered(self) -> None:
        self._count += 1

    def left(self) -> None:
        self._count -= 1


class TrackedOrderGateway:
    def __init__(self, inner: OrderGateway, in_flight: InFlightOrders) -> None:
        self._inner = inner
        self._in_flight = in_flight

    async def submit(self, request: PlaceOrderRequest) -> BrokerOrderAck:
        self._in_flight.entered()
        try:
            return await self._inner.submit(request)
        finally:
            self._in_flight.left()

    async def revoke(self, request: CancelOrderRequest) -> BrokerOrderAck:
        self._in_flight.entered()
        try:
            return await self._inner.revoke(request)
        finally:
            self._in_flight.left()

    async def find_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        self._in_flight.entered()
        try:
            return await self._inner.find_by_tag(client_tag)
        finally:
            self._in_flight.left()
