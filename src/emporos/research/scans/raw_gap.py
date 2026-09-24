"""The raw overnight gap held to the close, as a scan (EM-191, cell L3-raw-gap-hold-to-close).

One trade per name per day: when the session's first bar opens at least `gap_threshold_pct` away
from the previous close, enter at the close of bar `entry_bar` (1 = the 09:15 bar) in the declared
direction and hold to the session square-off. No stop, no target: the exit is the session's own.
The gap is the day's first-bar open against the previous session's last close as the data holds it,
so a weekend or holiday gap counts as one. A day whose 09:15 bar is missing is skipped.

Not parity-proven against an engine strategy (there is none), so its screens are advisory; the
tests pin every rule and the cell's S1 numbers are computed independently in the cell test.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import product

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import (
    EntryIntent,
    IntradayScan,
    ScanExecution,
    ScanIntent,
    entries_open,
)

__all__ = ["GapDirection", "RawGapParameters", "RawGapRules", "declared_arms", "raw_gap_scan"]

_SESSION_OPEN = time(9, 15)
_BAR = timedelta(minutes=5)


class GapDirection(StrEnum):
    CONTINUATION = "continuation"  # trade with the gap
    FADE = "fade"  # trade against it


@dataclass(frozen=True)
class RawGapParameters:
    gap_threshold_pct: Decimal
    direction: GapDirection
    entry_bar: int  # 1 = enter at the close of the 09:15 bar

    def __post_init__(self) -> None:
        if self.gap_threshold_pct <= 0:
            raise ValueError("the gap threshold must be positive")
        if self.entry_bar < 1:
            raise ValueError("the entry bar is numbered from 1")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> RawGapParameters:
        return cls(
            Decimal(point["gap_threshold_pct"]),
            GapDirection(point["direction"]),
            int(point["entry_bar"]),
        )

    def as_point(self) -> dict[str, str]:
        return {
            "gap_threshold_pct": str(self.gap_threshold_pct),
            "direction": self.direction.value,
            "entry_bar": str(self.entry_bar),
        }

    @property
    def entry_time(self) -> time:
        """The wall-clock START of the entry bar (its close is where the trade is signalled)."""
        start = datetime.combine(date(2000, 1, 3), _SESSION_OPEN) + _BAR * (self.entry_bar - 1)
        return start.time()


def declared_arms(grid: Mapping[str, tuple[str, ...]]) -> list[RawGapParameters]:
    """Every arm of the declared grid, in the order the declaration lists it. The declaration's
    parameter names must be exactly this cell's: an arm the declaration did not name is refused."""
    expected = ["gap_threshold_pct", "direction", "entry_bar"]
    if sorted(grid) != sorted(expected):
        raise ValueError(f"the declared grid must name exactly {expected}, not {sorted(grid)}")
    points = product(*(grid[name] for name in expected))
    return [RawGapParameters.from_point(dict(zip(expected, p, strict=True))) for p in points]


class RawGapRules:
    def __init__(self, parameters: RawGapParameters, no_new_entries_after: time) -> None:
        self._p = parameters
        self._cutoff = no_new_entries_after
        self._day: date | None = None
        self._day_open: Decimal | None = None
        self._previous_close: Decimal | None = None
        self._last_close: Decimal | None = None
        self._decided = False  # one look per day: the first entry-bar close decides it

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        moment = bar.ts.astimezone(IST)
        if moment.date() != self._day:
            self._day = moment.date()
            self._previous_close = self._last_close
            self._day_open = bar.open.amount if moment.time() == _SESSION_OPEN else None
            self._decided = False
        self._last_close = bar.close.amount
        if self._decided or held is not None or moment.time() != self._p.entry_time:
            return None
        self._decided = True  # whatever happens at this bar, the day is decided
        if not live or self._day_open is None or self._previous_close is None:
            return None
        gap_pct = (self._day_open / self._previous_close - 1) * 100
        if abs(gap_pct) < self._p.gap_threshold_pct:
            return None
        if not can_afford or not entries_open(bar, self._cutoff):
            return None
        up = gap_pct > 0
        buy = up if self._p.direction is GapDirection.CONTINUATION else not up
        return EntryIntent(OrderSide.BUY if buy else OrderSide.SELL)


def raw_gap_scan(parameters: RawGapParameters, execution: ScanExecution) -> IntradayScan:
    return IntradayScan(
        "raw_gap_hold_to_close",
        lambda: RawGapRules(parameters, execution.no_new_entries_after),
        execution,
    )
