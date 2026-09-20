"""The single point where a broker's order-writing methods are called.

Execution names these operations `submit`/`revoke`; only this file turns them into the broker's
`place_order`/`cancel_order`. An architecture test (`tests/unit/risk/test_no_bypass.py`) fails the
build if any other file outside the broker package calls an order-writing method, so "an order
reaches a broker only through execution" is a fact about the source tree.

`modify_order` is deliberately not exposed: repricing is cancel-then-replace (EM-99 F5).
"""

from typing import Protocol

from emporos.broker.models import (
    BrokerOrder,
    BrokerOrderAck,
    CancelOrderRequest,
    PlaceOrderRequest,
)


class BrokerOrderEndpoints(Protocol):
    """The slice of a `Broker` that execution needs."""

    async def place_order(self, request: PlaceOrderRequest) -> BrokerOrderAck: ...
    async def cancel_order(self, request: CancelOrderRequest) -> BrokerOrderAck: ...
    async def find_orders_by_tag(self, client_tag: str) -> list[BrokerOrder]: ...


class BrokerOrderGateway:
    def __init__(self, broker: BrokerOrderEndpoints) -> None:
        self._broker = broker

    async def submit(self, request: PlaceOrderRequest) -> BrokerOrderAck:
        return await self._broker.place_order(request)

    async def revoke(self, request: CancelOrderRequest) -> BrokerOrderAck:
        return await self._broker.cancel_order(request)

    async def find_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        return await self._broker.find_orders_by_tag(client_tag)
