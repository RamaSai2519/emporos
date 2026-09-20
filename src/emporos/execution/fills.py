"""Applying broker fills: idempotent, atomic, and the only place an order's fill quantity moves.

A fill is identified by the broker's trade id, which the executions collection holds unique, so a
redelivered fill after a reconnect is harmless: it is recognised and changes nothing. Applying one
writes the execution, the order (quantity, average price, state), its audit event and the position
in ONE transaction. The fills themselves are the source of truth for quantities: the order's
`filled_quantity` is only ever the sum of the fills applied to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from emporos.broker.models import BrokerTrade
from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.execution.ports import FillJournal, TradeBook, TradeCosts
from emporos.execution.state import OrderState, OrderStateMachine
from emporos.persistence.errors import ConcurrentModificationError
from emporos.persistence.records import ExecutionRecord, OrderRecord, PositionRecord
from emporos.persistence.transactions import FillOutcome
from emporos.portfolio.ledger import PositionCalculator

_ATTEMPTS = 3


class FillResult(StrEnum):
    APPLIED = "applied"  # a new fill: execution, order and position all written
    DUPLICATE = "duplicate"  # already applied: nothing changed
    UNATTRIBUTED = "unattributed"  # no order of ours has this broker order id (yet)
    REFUSED = "refused"  # applying it would be impossible (overfill): left for reconciliation


class FillProcessor:
    def __init__(
        self,
        journal: FillJournal,
        costs: TradeCosts,
        calculator: PositionCalculator,
        machine: OrderStateMachine,
        clock: Clock,
        ids: IdGenerator,
        account_id: str,
    ) -> None:
        self._journal = journal
        self._costs = costs
        self._calculator = calculator
        self._machine = machine
        self._clock = clock
        self._ids = ids
        self._account_id = account_id

    async def process(self, trade: BrokerTrade) -> FillResult:
        for _ in range(_ATTEMPTS):
            try:
                return await self._attempt(trade)
            except ConcurrentModificationError:
                continue  # the order moved under us (a cancel, a resolution): reload and redo
        raise ConcurrentModificationError(f"order for trade {trade.trade_id} kept changing")

    async def _attempt(self, trade: BrokerTrade) -> FillResult:
        if await self._journal.has_execution(trade.trade_id):
            return (
                FillResult.DUPLICATE
            )  # checked first: a redelivery must never look like an overfill
        order = await self._journal.by_broker_order_id(trade.broker_order_id)
        if order is None:
            return FillResult.UNATTRIBUTED
        if (trade.instrument_id, trade.side) != (order.instrument_id, order.side):
            return FillResult.REFUSED  # the broker's trade contradicts the order it names
        filled = order.filled_quantity + trade.quantity
        if filled > order.quantity:
            return FillResult.REFUSED
        now = self._clock.now()
        execution = ExecutionRecord(
            _id=self._ids.new_ulid(),
            broker_trade_id=trade.trade_id,
            order_id=order.id,
            instrument_id=trade.instrument_id,
            side=trade.side,
            quantity=trade.quantity,
            price=trade.price,
            ts=trade.executed_at or now,
            account_id=self._account_id,
            session_date=order.session_date,
            fees=self._costs.charges(trade),
            session_trade_no=await self._journal.fills_this_session(order.session_date) + 1,
        )
        held = await self._journal.position(trade.instrument_id)
        position = self._calculator.apply(held or self._flat(trade), execution)
        updated = self._filled(order, trade, filled)
        outcome = await self._journal.record_fill(order, updated, execution, position)
        return FillResult.APPLIED if outcome == FillOutcome.APPLIED else FillResult.DUPLICATE

    def _filled(self, order: OrderRecord, trade: BrokerTrade, filled: int) -> OrderRecord:
        state = OrderState(order.state)
        if state.terminal:
            target = state  # a fill after a cancel is a fact, not a transition
        elif filled == order.quantity:
            target = OrderState.FILLED
        elif state == OrderState.PENDING_CANCEL:
            target = state
        else:
            target = OrderState.PARTIALLY_FILLED
        self._machine.validate(state, target, order.filled_quantity, filled, order.quantity)
        previous_value = (order.average_price or Money.zero()).amount * order.filled_quantity
        average = Money((previous_value + trade.price.amount * trade.quantity) / Decimal(filled))
        return order.model_copy(
            update={
                "state": target.value,
                "filled_quantity": filled,
                "average_price": average,
                "updated_at": self._clock.now(),
                "status_message": f"fill {trade.trade_id}",
            }
        )

    def _flat(self, trade: BrokerTrade) -> PositionRecord:
        return PositionRecord(
            _id=f"{self._account_id}:{trade.instrument_id}",
            account_id=self._account_id,
            instrument_id=trade.instrument_id,
            net_quantity=0,
            average_price=Money.zero(),
            realised_pnl=Money.zero(),
            updated_at=self._clock.now(),
            gross_realised_pnl=Money.zero(),
            fees=Money.zero(),
        )


@dataclass(frozen=True)
class SyncResult:
    applied: int
    duplicates: int
    unattributed: int
    refused: int


class FillSynchroniser:
    """Bring every fill in the broker's trade book into the platform's books, once each.

    Safe to call at any time and as often as wanted: this is how a redelivered update, a missed
    update after a reconnect, and a fill that raced a cancel all end up reflected exactly once.
    """

    def __init__(self, book: TradeBook, processor: FillProcessor) -> None:
        self._book = book
        self._processor = processor

    async def sync(self) -> SyncResult:
        trades = sorted(await self._book.get_trade_book(), key=self._order)
        counts = {result: 0 for result in FillResult}
        for trade in trades:
            counts[await self._processor.process(trade)] += 1
        return SyncResult(
            counts[FillResult.APPLIED],
            counts[FillResult.DUPLICATE],
            counts[FillResult.UNATTRIBUTED],
            counts[FillResult.REFUSED],
        )

    @staticmethod
    def _order(trade: BrokerTrade) -> tuple[str, str]:
        stamp = trade.executed_at.isoformat() if trade.executed_at else ""
        return stamp, trade.trade_id
