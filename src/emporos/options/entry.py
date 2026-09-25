"""How an entry is planned from a day's chain (EM-226).

Three small collaborators, each replaceable: an `ExpiryChooser` picks the expiry, `EntryFilter`s
say whether a trade on that expiry may open today (trend, volatility band, event weeks all plug in
here), and a `SpreadTemplate` builds the spread from the chain. `SpreadEntry` composes them into
the one `EntryPlanner` the backtester asks. Selecting strikes needs a volatility number; that comes
from an injected `VolatilitySource`, never a global."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from typing import Protocol

from emporos.options.chain import ChainSnapshot, OptionRight
from emporos.options.spread import SpreadPlan, Vertical

__all__ = [
    "AlwaysOpen",
    "EntryFilter",
    "EntryPlanner",
    "ExpiryChooser",
    "IronCondorTemplate",
    "MonthlyExpiries",
    "PutCreditSpreadTemplate",
    "SigmaStrikes",
    "SpreadEntry",
    "SpreadTemplate",
    "TargetDte",
    "VolatilitySource",
]

_YEAR = Decimal(365)


class EntryFilter(Protocol):
    def allows(self, snapshot: ChainSnapshot, expiry: date) -> bool:
        """Whether a spread on `expiry` may be opened at this day's close."""
        ...


class AlwaysOpen:
    def allows(self, snapshot: ChainSnapshot, expiry: date) -> bool:
        return True


class VolatilitySource(Protocol):
    def annual_volatility(self, day: date) -> Decimal | None:
        """The annualised volatility (0.15 is 15%) known at that day's close, or none."""
        ...


class ExpiryChooser(Protocol):
    def choose(self, snapshot: ChainSnapshot) -> date | None: ...


@dataclass(frozen=True)
class TargetDte:
    """The listed expiry whose days-to-expiry lies in [`minimum`, `maximum`] and is closest to
    `target` (the earlier one on a tie)."""

    minimum: int
    maximum: int
    target: int

    def __post_init__(self) -> None:
        if not 1 <= self.minimum <= self.target <= self.maximum:
            raise ValueError("need 1 <= minimum <= target <= maximum")

    def choose(self, snapshot: ChainSnapshot) -> date | None:
        inside = [
            e for e in snapshot.expiry_dates if self.minimum <= snapshot.days_to(e) <= self.maximum
        ]
        if not inside:
            return None
        return min(inside, key=lambda e: (abs(snapshot.days_to(e) - self.target), e))


@dataclass(frozen=True)
class MonthlyExpiries:
    """The nearest MONTHLY expiry with between `minimum` and `maximum` days left. An option expiry
    is monthly when a future expires the same day, so the weekly expiries (which have no future)
    are left out. `futures` maps each day to the future expiries listed that day."""

    futures: Mapping[date, frozenset[date]]
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if not 1 <= self.minimum <= self.maximum:
            raise ValueError("need 1 <= minimum <= maximum")

    def choose(self, snapshot: ChainSnapshot) -> date | None:
        monthly = self.futures.get(snapshot.day, frozenset())
        inside = [
            e
            for e in snapshot.expiry_dates
            if e in monthly and self.minimum <= snapshot.days_to(e) <= self.maximum
        ]
        return min(inside) if inside else None


class SpreadTemplate(Protocol):
    def build(self, snapshot: ChainSnapshot, expiry: date) -> SpreadPlan | None: ...


@dataclass(frozen=True)
class SigmaStrikes:
    """Short strikes `sigmas` standard deviations out of the money, wings `width` points beyond.

    One standard deviation over the days left is `spot * volatility * sqrt(dte / 365)`; the short
    strike is that far below (puts) or above (calls) the spot, rounded AWAY from the money to the
    strike step, so the rounding never brings a short leg closer than asked."""

    volatility: VolatilitySource
    sigmas: Decimal
    width: Decimal

    def __post_init__(self) -> None:
        if self.sigmas <= 0 or self.width <= 0:
            raise ValueError("sigmas and width must be positive")

    def vertical(
        self, snapshot: ChainSnapshot, expiry: date, right: OptionRight
    ) -> Vertical | None:
        vol = self.volatility.annual_volatility(snapshot.day)
        if vol is None or vol <= 0:
            return None
        spread = (
            snapshot.underlying_close * vol * (Decimal(snapshot.days_to(expiry)) / _YEAR).sqrt()
        )
        step = snapshot.strike_step
        if right is OptionRight.PUT:
            raw = snapshot.underlying_close - self.sigmas * spread
            short = (raw / step).to_integral_value(ROUND_FLOOR) * step
            return Vertical(right, short, short - self.width)
        raw = snapshot.underlying_close + self.sigmas * spread
        short = -((-raw / step).to_integral_value(ROUND_FLOOR)) * step  # ceiling to the step
        return Vertical(right, short, short + self.width)


@dataclass(frozen=True)
class PutCreditSpreadTemplate:
    strikes: SigmaStrikes

    def build(self, snapshot: ChainSnapshot, expiry: date) -> SpreadPlan | None:
        put = self.strikes.vertical(snapshot, expiry, OptionRight.PUT)
        return None if put is None else SpreadPlan(expiry, (put,))


@dataclass(frozen=True)
class IronCondorTemplate:
    strikes: SigmaStrikes

    def build(self, snapshot: ChainSnapshot, expiry: date) -> SpreadPlan | None:
        put = self.strikes.vertical(snapshot, expiry, OptionRight.PUT)
        call = self.strikes.vertical(snapshot, expiry, OptionRight.CALL)
        if put is None or call is None:
            return None
        return SpreadPlan(expiry, (put, call))


class EntryPlanner(Protocol):
    def plan(self, snapshot: ChainSnapshot) -> SpreadPlan | None: ...


class SpreadEntry:
    def __init__(
        self,
        filters: Sequence[EntryFilter],
        chooser: ExpiryChooser,
        template: SpreadTemplate,
    ) -> None:
        self._filters = tuple(filters)
        self._chooser = chooser
        self._template = template

    def plan(self, snapshot: ChainSnapshot) -> SpreadPlan | None:
        expiry = self._chooser.choose(snapshot)
        if expiry is None or not all(f.allows(snapshot, expiry) for f in self._filters):
            return None
        return self._template.build(snapshot, expiry)
