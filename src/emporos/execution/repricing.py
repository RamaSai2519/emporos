"""Bounded cancel-confirm-replace; every replacement needs a fresh risk review."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import Clock
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.execution.fills import SyncResult
from emporos.execution.state import OrderState
from emporos.persistence.records import OrderRecord
from emporos.risk.approval import RiskApprovedSignal, RiskDecision, RiskRejection


@dataclass(frozen=True)
class Replacement:
    """A request to trade what is left of an order, at a new price, under a new key.

    Everything here is read from the persisted order, never from anything held in memory: repricing
    must work identically after a restart.
    """

    strategy_run_id: str
    instrument_id: str
    kind: str  # ENTRY or EXIT
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: Money
    reason: str
    key: str


class ReplacementReviewer(Protocol):
    """Sends a replacement back through risk: a new price is a new decision."""

    async def review(self, replacement: Replacement) -> RiskDecision: ...


class OrderExecutor(Protocol):
    async def place(
        self,
        approval: RiskApprovedSignal,
        *,
        parent_order_id: str | None = None,
        marketable: bool = True,
    ) -> OrderRecord: ...
    async def cancel(self, order_id: str) -> OrderRecord: ...
    async def refresh(self, order_id: str) -> OrderRecord: ...


class FillSource(Protocol):
    async def sync(self) -> SyncResult: ...


@dataclass(frozen=True)
class RepricePolicy:
    after: timedelta
    max_reprices: int
    max_chase_bps: Decimal

    def __post_init__(self) -> None:
        if self.after <= timedelta(0) or self.max_reprices < 0 or self.max_chase_bps < 0:
            raise ValueError("invalid repricing bounds")

    def validate(
        self,
        order: OrderRecord,
        count: int,
        original_price: Money,
        candidate: Money,
        age: timedelta,
    ) -> None:
        if order.order_type != OrderType.LIMIT:
            raise ValueError("stop-loss orders cannot be chased")
        if age < self.after or not 0 <= count < self.max_reprices:
            raise ValueError("repricing time or count bound reached")
        if original_price <= Money.zero() or candidate <= Money.zero():
            raise ValueError("repricing needs positive prices")
        chase = abs(candidate.amount - original_price.amount) / original_price.amount * 10000
        if chase > self.max_chase_bps:
            raise ValueError("maximum chase distance exceeded")


class RepriceCoordinator:
    def __init__(
        self,
        execution: OrderExecutor,
        risk: ReplacementReviewer,
        policy: RepricePolicy,
        clock: Clock,
        fills: FillSource,
    ) -> None:
        self._execution = execution
        self._risk = risk
        self._policy = policy
        self._clock = clock
        self._fills = fills

    async def reprice(self, order: OrderRecord, candidate: Money) -> OrderRecord | RiskRejection:
        """Cancel `order`, confirm it is really gone, and replace what is left at `candidate`."""
        if not order.strategy_run_id or not order.signal_kind:
            raise ValueError("only an order that came from a signal can be repriced")
        original_price = order.original_limit_price or order.limit_price
        self._policy.validate(
            order,
            order.reprice_count,
            original_price,
            candidate,
            self._clock.now() - order.created_at,
        )
        cancelled = await self._execution.cancel(order.id)
        if cancelled.state != OrderState.CANCELLED:
            # The broker may hold fills we have not applied yet: bring them in, then look again.
            await self._fills.sync()
            cancelled = await self._execution.refresh(order.id)
        if cancelled.state != OrderState.CANCELLED:
            return cancelled
        remaining = cancelled.quantity - cancelled.filled_quantity
        if remaining <= 0:
            return cancelled
        attempt = order.reprice_count + 1
        decision = await self._risk.review(
            Replacement(
                strategy_run_id=order.strategy_run_id,
                instrument_id=order.instrument_id,
                kind=order.signal_kind,
                side=order.side,
                order_type=order.order_type,
                quantity=remaining,
                limit_price=candidate,
                reason=f"reprice {attempt} of {order.id}",
                key=f"{order.id}:reprice:{attempt}",
            )
        )
        if isinstance(decision, RiskRejection):
            return decision
        return await self._execution.place(decision, parent_order_id=order.id, marketable=False)
