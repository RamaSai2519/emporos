"""The risk engine's vocabulary: a proposal in, an immutable snapshot of the book, a decision out
(PROFIT_PLAN §12.4, EM-240). Money is `Decimal` rupees throughout."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from emporos.eventtrader.stages.models import Side

__all__ = [
    "EntryProposal",
    "OpenPosition",
    "Product",
    "Refusal",
    "RiskDecision",
    "RiskSnapshot",
    "Sized",
]


class Product(StrEnum):
    INTRADAY = "intraday"  # cash, squared off at 15:15
    SWING = "swing"  # cash delivery, days to weeks
    OPTION = "option"  # bought outright; the premium is the whole risk


@dataclass(frozen=True)
class EntryProposal:
    """What the pipeline wants, before any size. A cash proposal carries its stop; an option
    proposal carries the premium per unit and the lot size (the premium IS the risk)."""

    name: str  # the instrument, e.g. "NSE:2885" or an option contract key
    product: Product
    side: Side
    entry_price: Decimal  # cash: the price; option: the premium per unit
    stop_price: Decimal | None  # required for cash, ignored for an option
    at: datetime
    lot_size: int = 1

    def __post_init__(self) -> None:
        if self.entry_price <= 0:
            raise ValueError("the entry price must be positive")
        if self.at.tzinfo is None:
            raise ValueError("a proposal needs a timezone-aware time")
        if self.lot_size < 1:
            raise ValueError("a lot is at least one unit")


@dataclass(frozen=True)
class OpenPosition:
    name: str
    product: Product
    side: Side
    quantity: int
    entry_price: Decimal
    stop_price: Decimal | None
    risk: Decimal  # rupees lost if the stop is hit (an option: the premium paid)
    marked_pnl: Decimal = Decimal(0)  # at the last mark, before costs


@dataclass(frozen=True)
class RiskSnapshot:
    """Everything the rules may read, frozen. A rule never reaches for the clock or the broker."""

    now: datetime
    starting_capital: Decimal
    realized_total: Decimal  # since the experiment began
    realized_today: Decimal
    open_positions: tuple[OpenPosition, ...] = ()
    posture_scale: Decimal = Decimal(1)  # 0 = hold, else a fraction of the risk budgets

    @property
    def open_risk(self) -> Decimal:
        return sum((p.risk for p in self.open_positions), Decimal(0))

    @property
    def open_pnl(self) -> Decimal:
        return sum((p.marked_pnl for p in self.open_positions), Decimal(0))

    def holds(self, name: str) -> bool:
        return any(p.name == name for p in self.open_positions)

    def count(self, *products: Product) -> int:
        return sum(1 for p in self.open_positions if p.product in products)


@dataclass(frozen=True)
class Sized:
    quantity: int  # shares (cash) or units = lots x lot_size (option)
    position_value: Decimal
    risk: Decimal  # rupees lost at the stop, or the premium paid

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValueError("a sized position has at least one unit")


@dataclass(frozen=True)
class Refusal:
    rule: str
    reason: str


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    sized: Sized | None = None
    refusals: tuple[Refusal, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.approved and (self.sized is None or self.refusals):
            raise ValueError("an approval carries a size and no refusals")
        if not self.approved and not self.refusals:
            raise ValueError("a refusal says why")
