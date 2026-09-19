"""What a strategy produces: an intent to trade, never an order (plan.md §9, §11-12).

A `Signal` is an immutable value. It carries WHAT the strategy wants — instrument, side, size,
the price it has in mind — and nothing about HOW it is sent. Risk decides whether it may go out;
execution turns it into a `PlaceOrderRequest`, with a client tag, a marketable-limit price and a
tick-size clamp. `OrderType` has no MARKET or IOC member, so a signal that asks for one cannot be
built (Decision 8); a raw string such as "MARKET" is refused at construction as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType


class SignalKind(StrEnum):
    """Entries open exposure and can be blocked (stale data, session cut-off); exits reduce it and
    must never be blocked by the same rules (plan.md §7 staleness)."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"


@dataclass(frozen=True)
class Signal:
    """`quantity` is a sizing HINT: risk may reduce or reject it. `limit_price` is the price the
    strategy has in mind, not necessarily the price sent: execution makes it marketable."""

    strategy_run_id: str
    instrument_id: str
    kind: SignalKind
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: Money
    ts: datetime  # the market event the strategy decided on, never wall-clock time
    reason: str
    trigger_price: Money | None = None

    def __post_init__(self) -> None:
        if not self.strategy_run_id or not self.instrument_id:
            raise ValueError("a signal needs a strategy run and an instrument")
        if not self.reason.strip():
            raise ValueError("a signal must say why: an unexplained signal is unauditable")
        self._require_enum("kind", self.kind, SignalKind)
        self._require_enum("side", self.side, OrderSide)
        self._require_enum("order_type", self.order_type, OrderType)
        if self.ts.tzinfo is None or self.ts.utcoffset() != UTC.utcoffset(None):
            raise ValueError("signal ts must be timezone-aware UTC")
        if self.quantity <= 0:
            raise ValueError("signal quantity must be positive")
        if self.limit_price <= Money.zero():
            raise ValueError("signal limit price must be positive")
        self._check_trigger()

    def _check_trigger(self) -> None:
        if self.order_type is OrderType.STOPLOSS_LIMIT:
            if self.trigger_price is None or self.trigger_price <= Money.zero():
                raise ValueError("a stop-loss limit signal needs a positive trigger price")
        elif self.trigger_price is not None:
            raise ValueError("only stop-loss limit signals take a trigger price")

    @staticmethod
    def _require_enum(name: str, value: object, kind: type[StrEnum]) -> None:
        if not isinstance(value, kind):
            raise TypeError(f"{name} must be a {kind.__name__}, got {value!r}")


class SignalSink(Protocol):
    """Where a strategy's signals go. Today: persistence; from Phase 11: risk, then execution."""

    async def submit(self, signal: Signal) -> None: ...
