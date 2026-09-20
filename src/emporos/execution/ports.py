"""Small interfaces execution depends on. Each consumer sees only what it calls (ISP)."""

from typing import Protocol

from emporos.broker.models import (
    BrokerOrder,
    BrokerOrderAck,
    BrokerTrade,
    CancelOrderRequest,
    PlaceOrderRequest,
)
from emporos.domain.money import Money
from emporos.persistence.records import (
    ExecutionRecord,
    OrderEventRecord,
    OrderRecord,
    PositionRecord,
)
from emporos.persistence.transactions import FillOutcome
from emporos.risk.approval import RiskApprovedSignal


class OrderGateway(Protocol):
    """The only route to a broker's order endpoints (`execution.gateway`)."""

    async def submit(self, request: PlaceOrderRequest) -> BrokerOrderAck: ...
    async def revoke(self, request: CancelOrderRequest) -> BrokerOrderAck: ...
    async def find_by_tag(self, client_tag: str) -> list[BrokerOrder]: ...


class OrderJournal(Protocol):
    """Durable order state. Every change lands together with its audit event."""

    async def get(self, order_id: str) -> OrderRecord | None: ...
    async def by_key(self, key: str) -> OrderRecord | None: ...
    async def by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None: ...
    async def unresolved(self, instrument_id: str) -> bool: ...
    async def active(self) -> list[OrderRecord]: ...
    async def create(self, order: OrderRecord) -> None: ...
    async def transition(self, previous: OrderRecord, updated: OrderRecord) -> None: ...
    async def history(self, order_id: str) -> list[OrderEventRecord]: ...


class OrderPricer(Protocol):
    """Decides the price actually sent. Refuses one the exchange would."""

    async def price(
        self, approval: RiskApprovedSignal, *, marketable: bool
    ) -> tuple[Money, Money | None]: ...


class TickSizes(Protocol):
    async def tick_size(self, instrument_id: str) -> Money: ...


class FillJournal(Protocol):
    """Atomic application of one fill: execution + order + position + audit event."""

    async def by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None: ...
    async def has_execution(self, broker_trade_id: str) -> bool: ...
    async def position(self, instrument_id: str) -> PositionRecord | None: ...
    async def fills_this_session(self, session_date: str) -> int: ...
    async def record_fill(
        self,
        previous: OrderRecord,
        updated: OrderRecord,
        execution: ExecutionRecord,
        position: PositionRecord,
    ) -> FillOutcome: ...


class TradeCosts(Protocol):
    def charges(self, trade: BrokerTrade) -> Money: ...


class TradeBook(Protocol):
    async def get_trade_book(self) -> list[BrokerTrade]: ...
