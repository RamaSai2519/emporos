"""The opening-range breakout on high relative volume and a wide range (EM-191, cell
L3-orb-high-rvol-wide-range).

One trade per name per day. The opening range is the bars that START in 09:15..09:44. When it is
complete the day is qualified or not, from what is known at 09:45: its total volume against the mean
of the same figure over the name's previous `HISTORY_DAYS` sessions (at least `MIN_HISTORY`),
and its width in bps of the price. The first later bar that closes 5 bps beyond the range on a
qualified day is the breakout, taken in its direction. No stop, no target: the exit is the
session square-off (`close`) or the first bar closing at or after 12:30 (`1230`).

A day whose opening range is missing a bar (or holds a partial one) is skipped and does not enter
the volume history. Not parity-proven against an engine strategy, so its screens are advisory.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from itertools import product

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import (
    EntryIntent,
    ExitIntent,
    IntradayScan,
    ScanExecution,
    ScanIntent,
    entries_open,
)

__all__ = ["OrbExit", "OrbRvolParameters", "OrbRvolRules", "declared_arms", "orb_rvol_scan"]

_RANGE_END = time(9, 45)  # a bar starting before this belongs to the opening range
_RANGE_BARS = 6
_BUFFER_BPS = Decimal(5)
_BPS = Decimal(10_000)
_TIME_STOP = time(12, 30)
HISTORY_DAYS = 20
MIN_HISTORY = 10


class OrbExit(StrEnum):
    CLOSE = "close"  # held to the session square-off
    MIDDAY = "1230"  # first bar closing at or after 12:30


@dataclass(frozen=True)
class OrbRvolParameters:
    rvol_min: Decimal
    or_width_min_bps: Decimal
    exit: OrbExit

    def __post_init__(self) -> None:
        if self.rvol_min <= 0 or self.or_width_min_bps < 0:
            raise ValueError("rvol_min must be positive and the width floor non-negative")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> OrbRvolParameters:
        return cls(
            Decimal(point["rvol_min"]),
            Decimal(point["or_width_min_bps"]),
            OrbExit(point["exit"]),
        )

    def as_point(self) -> dict[str, str]:
        return {
            "rvol_min": str(self.rvol_min),
            "or_width_min_bps": str(self.or_width_min_bps),
            "exit": self.exit.value,
        }


def declared_arms(grid: Mapping[str, tuple[str, ...]]) -> list[OrbRvolParameters]:
    """Every arm of the declared grid. The declaration must name exactly this cell's parameters."""
    expected = ["rvol_min", "or_width_min_bps", "exit"]
    if sorted(grid) != sorted(expected):
        raise ValueError(f"the declared grid must name exactly {expected}, not {sorted(grid)}")
    points = product(*(grid[name] for name in expected))
    return [OrbRvolParameters.from_point(dict(zip(expected, p, strict=True))) for p in points]


class OrbRvolRules:
    def __init__(self, parameters: OrbRvolParameters, no_new_entries_after: time) -> None:
        self._p = parameters
        self._cutoff = no_new_entries_after
        self._history: deque[int] = deque(maxlen=HISTORY_DAYS)
        self._day: date | None = None
        self._reset_day()

    def _reset_day(self) -> None:
        self._range_bars = 0
        self._range_valid = True
        self._range_volume = 0
        self._high: Decimal | None = None
        self._low: Decimal | None = None
        self._qualified: bool | None = None  # decided once, when the range completes
        self._decided = False

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        moment = bar.ts.astimezone(IST)
        if moment.date() != self._day:
            self._day = moment.date()
            self._reset_day()
        if moment.time() < _RANGE_END:
            self._add_to_range(bar)
            return None
        if self._qualified is None:
            self._qualified = self._qualify()
        if held is not None:
            return self._manage(bar)
        if not self._qualified or self._decided or not live:
            return None
        return self._maybe_enter(bar, can_afford)

    def _add_to_range(self, bar: Candle) -> None:
        self._range_bars += 1
        self._range_valid = self._range_valid and not bar.partial
        self._range_volume += bar.volume
        high, low = bar.high.amount, bar.low.amount
        self._high = high if self._high is None else max(self._high, high)
        self._low = low if self._low is None else min(self._low, low)

    def _qualify(self) -> bool:
        """Decide the day from the completed opening range, then record its volume."""
        if not self._range_valid or self._range_bars != _RANGE_BARS:
            return False
        assert self._high is not None and self._low is not None
        history = list(self._history)
        self._history.append(self._range_volume)
        if len(history) < MIN_HISTORY:
            return False
        mean = Decimal(sum(history)) / len(history)
        if mean <= 0:
            return False
        rvol = Decimal(self._range_volume) / mean
        mid = (self._high + self._low) / 2
        width_bps = (self._high - self._low) / mid * _BPS
        return rvol >= self._p.rvol_min and width_bps >= self._p.or_width_min_bps

    def _manage(self, bar: Candle) -> ScanIntent:
        if self._p.exit is OrbExit.MIDDAY and bar.closes_at.astimezone(IST).time() >= _TIME_STOP:
            return ExitIntent()
        return None

    def _maybe_enter(self, bar: Candle, can_afford: bool) -> ScanIntent:
        assert self._high is not None and self._low is not None
        close = bar.close.amount
        buffer = _BUFFER_BPS / _BPS
        if close > self._high * (1 + buffer):
            side = OrderSide.BUY
        elif close < self._low * (1 - buffer):
            side = OrderSide.SELL
        else:
            return None
        self._decided = True  # the first breakout is the day's only look, taken or not
        if not can_afford or not entries_open(bar, self._cutoff):
            return None
        return EntryIntent(side)


def orb_rvol_scan(parameters: OrbRvolParameters, execution: ScanExecution) -> IntradayScan:
    return IntradayScan(
        "orb_rvol_wide_range",
        lambda: OrbRvolRules(parameters, execution.no_new_entries_after),
        execution,
    )
