"""Execution test doubles with observable broker and journal boundaries.

Each implements the same Protocol as the production collaborator (`emporos.execution.ports`), so a
test drives the real engine, not a patched copy of it.
"""

from dataclasses import replace
from datetime import datetime

from emporos.broker.errors import BrokerRejectedError
from emporos.broker.models import (
    BrokerOrder,
    BrokerOrderAck,
    BrokerOrderStatus,
    BrokerTrade,
    CancelOrderRequest,
    PlaceOrderRequest,
)
from emporos.domain.money import Money
from emporos.persistence.errors import ConcurrentModificationError, DuplicateRecordError
from emporos.persistence.records import (
    ExecutionRecord,
    OrderEventRecord,
    OrderRecord,
    PositionRecord,
)
from emporos.persistence.transactions import FillOutcome


class FixedTicks:
    def __init__(self, tick: str = "0.05") -> None:
        self._tick = Money.of(tick)

    async def tick_size(self, instrument_id: str) -> Money:
        return self._tick


class MemoryOrderJournal:
    """The guarantees of `MongoOrderJournal`: unique keys, optimistic writes,
    and an event per change."""

    def __init__(self) -> None:
        self.orders: dict[str, OrderRecord] = {}
        self.events: list[OrderRecord] = []
        self.executions: dict[str, ExecutionRecord] = {}
        self.positions: dict[str, PositionRecord] = {}
        self.event_log: dict[str, list[OrderEventRecord]] = {}

    async def get(self, order_id: str) -> OrderRecord | None:
        return self.orders.get(order_id)

    async def by_key(self, key: str) -> OrderRecord | None:
        return next((o for o in self.orders.values() if o.idempotency_key == key), None)

    async def by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None:
        return next((o for o in self.orders.values() if o.broker_order_id == broker_order_id), None)

    async def unresolved(self, instrument_id: str) -> bool:
        return any(
            o.instrument_id == instrument_id and o.state in ("UNKNOWN", "PENDING_NEW")
            for o in self.orders.values()
        )

    async def active(self) -> list[OrderRecord]:
        return [
            o for o in self.orders.values() if o.state not in ("FILLED", "REJECTED", "CANCELLED")
        ]

    async def history(self, order_id: str) -> list[OrderEventRecord]:
        return list(self.event_log.get(order_id, []))

    async def create(self, order: OrderRecord) -> None:
        if await self.by_key(order.idempotency_key):
            raise DuplicateRecordError("orders", "idempotency_key", ("idempotency_key",))
        self.orders[order.id] = order
        self._log(order)

    async def transition(self, previous: OrderRecord, updated: OrderRecord) -> None:
        if self.orders[previous.id] != previous:
            raise ConcurrentModificationError("order changed concurrently")
        self.orders[updated.id] = updated
        self._log(updated)

    async def has_execution(self, broker_trade_id: str) -> bool:
        return broker_trade_id in self.executions

    async def position(self, instrument_id: str) -> PositionRecord | None:
        return self.positions.get(instrument_id)

    async def fills_this_session(self, session_date: str) -> int:
        return sum(1 for e in self.executions.values() if e.session_date == session_date)

    async def record_fill(
        self,
        previous: OrderRecord,
        updated: OrderRecord,
        execution: ExecutionRecord,
        position: PositionRecord,
    ) -> FillOutcome:
        if execution.broker_trade_id in self.executions:
            return FillOutcome.DUPLICATE
        await self.transition(previous, updated)
        self.executions[execution.broker_trade_id] = execution
        self.positions[position.instrument_id] = position
        return FillOutcome.APPLIED

    def _log(self, order: OrderRecord) -> None:
        self.events.append(order)
        log = self.event_log.setdefault(order.id, [])
        log.append(
            OrderEventRecord(
                _id=f"{order.id}:{len(log) + 1}",
                order_id=order.id,
                seq=len(log) + 1,
                ts=order.updated_at,
                state=order.state,
                filled_quantity=order.filled_quantity,
                reason=order.status_message,
            )
        )


class FaultGateway:
    """A broker as execution sees it: places what it is told, can fail on cue, keeps books."""

    def __init__(self, journal: MemoryOrderJournal, error: Exception | None = None) -> None:
        self.journal = journal
        self.error = error
        self.placements: list[PlaceOrderRequest] = []
        self.orders: list[BrokerOrder] = []
        self.trades: list[BrokerTrade] = []
        self.cancel_error: Exception | None = None
        self.lookup_error: Exception | None = None

    async def submit(self, request: PlaceOrderRequest) -> BrokerOrderAck:
        assert any(o.ordertag == request.client_tag for o in self.journal.orders.values())
        self.placements.append(request)
        self.orders.append(
            BrokerOrder(
                broker_order_id=str(len(self.placements)),
                client_tag=request.client_tag,
                instrument_id=request.instrument_id,
                side=request.side,
                order_type=request.order_type,
                quantity=request.quantity,
                filled_quantity=0,
                status=BrokerOrderStatus.OPEN,
                price=request.price,
                trigger_price=request.trigger_price,
            )
        )
        if self.error:
            raise self.error
        return BrokerOrderAck(self.orders[-1].broker_order_id, request.client_tag)

    async def revoke(self, request: CancelOrderRequest) -> BrokerOrderAck:
        if self.cancel_error:
            raise self.cancel_error
        held = next((o for o in self.orders if o.broker_order_id == request.broker_order_id), None)
        if held is not None and held.status == BrokerOrderStatus.FILLED:
            raise BrokerRejectedError("order already completed")
        self.orders = [
            replace(o, status=BrokerOrderStatus.CANCELLED)
            if o.broker_order_id == request.broker_order_id
            else o
            for o in self.orders
        ]
        return BrokerOrderAck(request.broker_order_id)

    async def find_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        if self.lookup_error:
            raise self.lookup_error
        return [o for o in self.orders if o.client_tag == client_tag]

    async def get_trade_book(self) -> list[BrokerTrade]:
        return list(self.trades)

    def fill(self, index: int, quantity: int, price: str, trade_id: str, at: datetime) -> None:
        """The broker fills `quantity` of one of its orders: order book and trade book move."""
        order = self.orders[index]
        filled = order.filled_quantity + quantity
        self.orders[index] = replace(
            order,
            filled_quantity=filled,
            status=(
                BrokerOrderStatus.FILLED
                if filled == order.quantity
                else BrokerOrderStatus.PARTIALLY_FILLED
            ),
        )
        self.trades.append(
            BrokerTrade(
                trade_id, order.broker_order_id, order.instrument_id, order.side, quantity,
                Money.of(price), at,
            )
        )  # fmt: skip


class RecordingLimiter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def acquire(self, group: str) -> None:
        self.calls.append(group)


class ZeroCosts:
    def charges(self, trade: BrokerTrade) -> Money:
        return Money.zero()
