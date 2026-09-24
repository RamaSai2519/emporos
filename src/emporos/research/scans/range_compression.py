"""A break of a narrow-range or inside session's extreme, held to the close (EM-191, cell
L4-nr7-inside-day-breakout).

One trade per name per day. Each session is summarised by its high, its low and its range as a
share of its close, from the 5m bars themselves. A session with fewer than `MIN_BARS` bars is not a
day: it is not recorded and it breaks the run, so a condition never spans a hole. On a session
after one that meets the condition, the first bar closing `buffer_bps` beyond the previous
session's high is a long and beyond its low a short; enter at that close and hold to the
square-off (no stop, no target).

* `nr4` / `nr7`: the previous session's range is strictly the smallest of the last 4 / 7
  sessions, itself included (all consecutive and complete).
* `inside`: the previous session's high is below the one before it and its low is above it.

Not parity-proven against an engine strategy, so its screens are advisory.
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
    IntradayScan,
    ScanExecution,
    ScanIntent,
    entries_open,
)

__all__ = [
    "Compression",
    "CompressionParameters",
    "CompressionRules",
    "declared_arms",
    "range_compression_scan",
]

MIN_BARS = 70  # of the 75 five-minute bars in a session
_BPS = Decimal(10_000)
_HISTORY = 7


class Compression(StrEnum):
    NR4 = "nr4"
    NR7 = "nr7"
    INSIDE = "inside"


@dataclass(frozen=True)
class CompressionParameters:
    condition: Compression
    buffer_bps: Decimal

    def __post_init__(self) -> None:
        if self.buffer_bps < 0:
            raise ValueError("the buffer cannot be negative")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> CompressionParameters:
        return cls(Compression(point["condition"]), Decimal(point["buffer_bps"]))

    def as_point(self) -> dict[str, str]:
        return {"condition": self.condition.value, "buffer_bps": str(self.buffer_bps)}


def declared_arms(grid: Mapping[str, tuple[str, ...]]) -> list[CompressionParameters]:
    """Every arm of the declared grid. The declaration must name exactly this cell's parameters."""
    expected = ["condition", "buffer_bps"]
    if sorted(grid) != sorted(expected):
        raise ValueError(f"the declared grid must name exactly {expected}, not {sorted(grid)}")
    points = product(*(grid[name] for name in expected))
    return [CompressionParameters.from_point(dict(zip(expected, p, strict=True))) for p in points]


@dataclass(frozen=True)
class _Session:
    high: Decimal
    low: Decimal
    range_share: Decimal


class CompressionRules:
    def __init__(self, parameters: CompressionParameters, no_new_entries_after: time) -> None:
        self._p = parameters
        self._cutoff = no_new_entries_after
        self._history: deque[_Session] = deque(maxlen=_HISTORY)
        self._day: date | None = None
        self._bars = 0
        self._high: Decimal | None = None
        self._low: Decimal | None = None
        self._close: Decimal | None = None
        self._armed = False
        self._decided = False

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        moment = bar.ts.astimezone(IST)
        if moment.date() != self._day:
            self._roll(moment.date())
        self._bars += 1
        self._high = bar.high.amount if self._high is None else max(self._high, bar.high.amount)
        self._low = bar.low.amount if self._low is None else min(self._low, bar.low.amount)
        self._close = bar.close.amount
        if not self._armed or self._decided or held is not None or not live:
            return None
        previous = self._history[-1]
        buffer = self._p.buffer_bps / _BPS
        close = bar.close.amount
        if close > previous.high * (1 + buffer):
            side = OrderSide.BUY
        elif close < previous.low * (1 - buffer):
            side = OrderSide.SELL
        else:
            return None
        self._decided = True  # the first break is the day's only look, taken or not
        if not can_afford or not entries_open(bar, self._cutoff):
            return None
        return EntryIntent(side)

    def _roll(self, day: date) -> None:
        """Close the finished session into the history and decide today's condition."""
        if self._day is not None:
            self._record_finished_session()
        self._day, self._bars = day, 0
        self._high = self._low = self._close = None
        self._decided = False
        self._armed = self._condition_met()

    def _record_finished_session(self) -> None:
        if self._bars < MIN_BARS or self._high is None or self._low is None or not self._close:
            self._history.clear()  # a hole in the data breaks the run of consecutive sessions
            return
        share = (self._high - self._low) / self._close
        self._history.append(_Session(self._high, self._low, share))

    def _condition_met(self) -> bool:
        history = list(self._history)
        match self._p.condition:
            case Compression.INSIDE:
                if len(history) < 2:
                    return False
                before, last = history[-2], history[-1]
                return last.high < before.high and last.low > before.low
            case Compression.NR4 | Compression.NR7:
                window = 4 if self._p.condition is Compression.NR4 else 7
                if len(history) < window:
                    return False
                recent = history[-window:]
                return all(recent[-1].range_share < s.range_share for s in recent[:-1])


def range_compression_scan(
    parameters: CompressionParameters, execution: ScanExecution
) -> IntradayScan:
    return IntradayScan(
        "range_compression_breakout",
        lambda: CompressionRules(parameters, execution.no_new_entries_after),
        execution,
    )
