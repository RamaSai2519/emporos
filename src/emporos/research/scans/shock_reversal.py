"""A gap that the first hour has started to retrace, faded to the close (EM-191, cell
L3-first-hour-shock-reversal).

One trade per name per day. The shock is the raw gap: the 09:15 bar opens at least
`gap_threshold_pct` away from the previous session's last close. At the close of the bar that starts
at 10:10 (the 12th bar, the end of the first hour) the retrace is
(day open - close) / (day open - previous close): 0 at the open, 1 with the whole gap given back,
above 1 through it. If it is at least `retrace_min`, enter at that close in the direction of the
retrace (short a gap up, long a gap down) and hold to the session square-off. Otherwise the day is
not traded. A day whose 09:15 or 10:10 bar is missing is skipped.

Not parity-proven against an engine strategy, so its screens are advisory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
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

__all__ = ["ShockReversalParameters", "ShockReversalRules", "declared_arms", "shock_reversal_scan"]

_SESSION_OPEN = time(9, 15)
_DECISION = time(10, 10)  # the START of the bar whose close ends the first hour


@dataclass(frozen=True)
class ShockReversalParameters:
    gap_threshold_pct: Decimal
    retrace_min: Decimal

    def __post_init__(self) -> None:
        if self.gap_threshold_pct <= 0 or self.retrace_min <= 0:
            raise ValueError("the gap threshold and the retrace floor must be positive")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> ShockReversalParameters:
        return cls(Decimal(point["gap_threshold_pct"]), Decimal(point["retrace_min"]))

    def as_point(self) -> dict[str, str]:
        return {
            "gap_threshold_pct": str(self.gap_threshold_pct),
            "retrace_min": str(self.retrace_min),
        }


def declared_arms(grid: Mapping[str, tuple[str, ...]]) -> list[ShockReversalParameters]:
    """Every arm of the declared grid. The declaration must name exactly this cell's parameters."""
    expected = ["gap_threshold_pct", "retrace_min"]
    if sorted(grid) != sorted(expected):
        raise ValueError(f"the declared grid must name exactly {expected}, not {sorted(grid)}")
    points = product(*(grid[name] for name in expected))
    return [ShockReversalParameters.from_point(dict(zip(expected, p, strict=True))) for p in points]


class ShockReversalRules:
    def __init__(self, parameters: ShockReversalParameters, no_new_entries_after: time) -> None:
        self._p = parameters
        self._cutoff = no_new_entries_after
        self._day: date | None = None
        self._day_open: Decimal | None = None
        self._previous_close: Decimal | None = None
        self._last_close: Decimal | None = None

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        moment = bar.ts.astimezone(IST)
        if moment.date() != self._day:
            self._day = moment.date()
            self._previous_close = self._last_close
            self._day_open = bar.open.amount if moment.time() == _SESSION_OPEN else None
        self._last_close = bar.close.amount
        if held is not None or moment.time() != _DECISION:
            return None
        if not live or self._day_open is None or self._previous_close is None:
            return None
        move = self._day_open - self._previous_close
        if abs(move) / self._previous_close * 100 < self._p.gap_threshold_pct:
            return None
        retrace = (self._day_open - bar.close.amount) / move
        if retrace < self._p.retrace_min:
            return None
        if not can_afford or not entries_open(bar, self._cutoff):
            return None
        return EntryIntent(OrderSide.SELL if move > 0 else OrderSide.BUY)


def shock_reversal_scan(
    parameters: ShockReversalParameters, execution: ScanExecution
) -> IntradayScan:
    return IntradayScan(
        "first_hour_shock_reversal",
        lambda: ShockReversalRules(parameters, execution.no_new_entries_after),
        execution,
    )
