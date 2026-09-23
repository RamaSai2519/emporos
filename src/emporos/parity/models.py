"""Immutable values for one parity comparison: a signal on either side, how the two lined up, and
what became of each."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind


class SignalOrigin(StrEnum):
    PAPER = "paper"
    BACKTEST = "backtest"


class ParityStatus(StrEnum):
    """One value per way a signal can end up when the two sides are compared."""

    MATCHED = "MATCHED"  # both signalled, and both ended the same way (filled, or neither did)
    PAPER_ONLY_SIGNAL = "PAPER_ONLY_SIGNAL"  # paper signalled; the backtest did not
    BACKTEST_ONLY_SIGNAL = "BACKTEST_ONLY_SIGNAL"  # the backtest signalled; paper did not
    RISK_REJECTED = "RISK_REJECTED"  # paper's risk engine blocked it
    MISSED = "MISSED"  # paper's order expired or was cancelled unfilled; the backtest filled
    PARTIAL = "PARTIAL"  # either side filled only part of what it ordered
    FILLED_BACKTEST_NOT_PAPER = "FILLED_BACKTEST_NOT_PAPER"
    FILLED_PAPER_NOT_BACKTEST = "FILLED_PAPER_NOT_BACKTEST"


@dataclass(frozen=True)
class SignalPoint:
    """One signal, in the shape both sides share. `ref` names it in its own world: a signal id
    for paper, a 1-based journal sequence for the backtest."""

    origin: SignalOrigin
    ref: str
    sequence: int
    instrument_id: str
    kind: SignalKind
    side: OrderSide
    ts: datetime
    price: Money
    quantity: int
    system: bool = False  # the session's own forced exit rather than the strategy's decision

    @property
    def key(self) -> tuple[str, OrderSide, SignalKind]:
        return (self.instrument_id, self.side, self.kind)


@dataclass(frozen=True)
class SignalMatch:
    paper: SignalPoint | None
    backtest: SignalPoint | None

    def __post_init__(self) -> None:
        if self.paper is None and self.backtest is None:
            raise ValueError("a match needs at least one side")

    @property
    def matched(self) -> bool:
        return self.paper is not None and self.backtest is not None


@dataclass(frozen=True)
class LatencyProfile:
    """Where the time between deciding and being filled went (paper only: a backtest has none)."""

    decision: timedelta | None  # the market event -> the order was created
    placement: timedelta | None  # created -> the broker first acknowledged it
    fill: timedelta | None  # acknowledged -> first fill
    reprices: int  # cancel-then-replace rounds


class ReferenceKind(StrEnum):
    MID = "mid"
    LTP = "ltp"
    SIGNAL_PRICE = "signal_price"  # no quote was on record: the signal's own price


@dataclass(frozen=True)
class Slippage:
    """Adverse cost of a fill against a reference price, in basis points: positive is worse for us
    (paid above / sold below the reference)."""

    bps: Decimal
    reference: Money
    kind: ReferenceKind


@dataclass(frozen=True)
class SideOutcome:
    """What became of one signal on one side."""

    ordered_quantity: int
    filled_quantity: int
    average_fill_price: Money | None
    charges: Money
    rejected_by_risk: bool = False
    latency: LatencyProfile | None = None
    slippage: Slippage | None = None

    @property
    def fully_filled(self) -> bool:
        return self.ordered_quantity > 0 and self.filled_quantity >= self.ordered_quantity

    @property
    def partly_filled(self) -> bool:
        return 0 < self.filled_quantity < self.ordered_quantity
