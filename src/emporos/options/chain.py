"""One trading day's option chain for one underlying, as end-of-day data gives it (EM-226).

What an end-of-day archive holds per contract: the close (last traded price), the exchange's
settlement price, open interest and contracts traded. It holds NO intraday prices and NO bid-ask, so
nothing here can say what a fill cost beyond the slippage scenario's assumption."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

__all__ = ["ChainSnapshot", "ExpiryChain", "OptionQuote", "OptionRight"]


class OptionRight(StrEnum):
    PUT = "PE"
    CALL = "CE"


@dataclass(frozen=True)
class OptionQuote:
    strike: Decimal
    right: OptionRight
    close: Decimal  # last traded price of the day; meaningless when nothing traded
    settle: Decimal  # the exchange's settlement price: defined even when nothing traded
    open_interest: int
    contracts_traded: int

    def __post_init__(self) -> None:
        if self.strike <= 0:
            raise ValueError("a strike must be positive")
        if self.close < 0 or self.settle < 0:
            raise ValueError("a premium cannot be negative")
        if self.open_interest < 0 or self.contracts_traded < 0:
            raise ValueError("open interest and contracts traded cannot be negative")

    @property
    def tradable(self) -> bool:
        """A contract that did not trade has no close to fill at: an order cannot be assumed
        done."""
        return self.contracts_traded > 0 and self.close > 0

    @property
    def mark(self) -> Decimal:
        """The value used to mark a position: the close when it traded, else the settlement."""
        return self.close if self.tradable else self.settle


@dataclass(frozen=True)
class ExpiryChain:
    expiry: date
    quotes: Mapping[tuple[Decimal, OptionRight], OptionQuote]
    # the lot size of THIS expiry's contracts, when it differs from the snapshot's: an exchange
    # changes a lot size for new expiries only, so two expiries listed on one day can differ
    lot_size: int | None = None

    def quote(self, strike: Decimal, right: OptionRight) -> OptionQuote | None:
        return self.quotes.get((strike, right))

    @property
    def strikes(self) -> tuple[Decimal, ...]:
        return tuple(sorted({strike for strike, _ in self.quotes}))


@dataclass(frozen=True)
class ChainSnapshot:
    """Everything known at one day's close: the underlying, the contract specification IN FORCE that
    day (lot size and strike step changed over time) and every listed expiry."""

    day: date
    underlying: str
    underlying_close: Decimal
    lot_size: int
    strike_step: Decimal
    tick_size: Decimal
    expiries: Mapping[date, ExpiryChain]
    # the exchange's final settlement level for each expiry that falls ON this day (an index
    # option settles on it); empty on days nothing expires or when the source cannot say
    settlements: Mapping[date, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.underlying_close <= 0 or self.strike_step <= 0 or self.tick_size <= 0:
            raise ValueError("the underlying close, strike step and tick size must be positive")
        if self.lot_size < 1:
            raise ValueError("a lot holds at least one unit")

    @property
    def expiry_dates(self) -> tuple[date, ...]:
        return tuple(sorted(self.expiries))

    def lot_for(self, expiry: date) -> int:
        """The lot size of that expiry's contracts (the snapshot's own when it has no override)."""
        chain = self.expiries.get(expiry)
        return self.lot_size if chain is None or chain.lot_size is None else chain.lot_size

    def days_to(self, expiry: date) -> int:
        return (expiry - self.day).days
