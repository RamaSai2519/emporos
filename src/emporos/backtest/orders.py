"""Order and fill values for the simulated broker (plan.md §10).

A `SimOrderRequest` can only be a LIMIT or STOPLOSS_LIMIT order: `OrderType` has no other member,
so a market or IOC order is unrepresentable here as everywhere else (Decision 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate
from emporos.domain.orders import OrderSide, OrderType


class FillReason(StrEnum):
    MATCHED = "MATCHED"  # a resting order that a bar traded through
    FORCED_SQUARE_OFF = "FORCED_SQUARE_OFF"  # the broker's end-of-day square-off, not our order


@dataclass(frozen=True)
class SimOrderRequest:
    instrument_id: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: Money
    tag: str  # the client tag: unique per order, so a resend is refused, never duplicated
    strategy_run_id: str  # EM-158: which strategy's signal this order came from
    trigger_price: Money | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id or not self.tag:
            raise ValueError("an order needs an instrument and a tag")
        if not self.strategy_run_id:
            raise ValueError("an order needs a strategy_run_id")
        if not isinstance(self.side, OrderSide) or not isinstance(self.order_type, OrderType):
            raise TypeError("side and order_type must be OrderSide / OrderType members")
        if self.quantity <= 0:
            raise ValueError("an order has a positive quantity")
        if self.limit_price <= Money.zero():
            raise ValueError("an order has a positive limit price")
        self._check_trigger()

    def _check_trigger(self) -> None:
        if self.order_type is OrderType.LIMIT:
            if self.trigger_price is not None:
                raise ValueError("only stop-loss limit orders take a trigger price")
            return
        if self.trigger_price is None or self.trigger_price <= Money.zero():
            raise ValueError("a stop-loss limit order needs a positive trigger price")
        # A stop-loss on a long (SELL) triggers on the way down: its limit sits at or below the
        # trigger. A stop on a short (BUY) is the mirror image.
        wrong_way = (
            self.limit_price > self.trigger_price
            if self.side is OrderSide.SELL
            else self.limit_price < self.trigger_price
        )
        if wrong_way:
            raise ValueError("the limit price is on the wrong side of the trigger price")


@dataclass(frozen=True)
class Fill:
    sequence: int  # broker-wide, monotonic: the trade id
    order_id: str
    tag: str
    instrument_id: str
    side: OrderSide
    quantity: int
    price: Money
    ts: datetime
    strategy_run_id: str  # EM-158: which strategy's order this fill settles
    reason: FillReason = FillReason.MATCHED

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() != UTC.utcoffset(None):
            raise ValueError("a fill ts must be timezone-aware UTC")
        if not self.strategy_run_id:
            raise ValueError("a fill needs a strategy_run_id")
        if self.quantity <= 0:
            raise ValueError("a fill has a positive quantity")


@dataclass(frozen=True)
class SimEvent:
    """One order changing state, with the fill that caused it (if any)."""

    order_id: str
    update: OrderUpdate
    fill: Fill | None = None
