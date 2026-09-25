"""A results filing published DURING the session, and the drift after it (EM-231, cell
L2-in-session-results-drift; config/experiments/l2-in-session-results-drift.yaml).

Every earlier L2 cell used only filings published outside the session and skipped the rest. This
one trades the rest. Per name and event, from the filing's exchange dissemination time
`public_at` (never from a price):

* the event counts only if `public_at` is between 09:15 and 14:30 IST on a weekday;
* the ANCHOR is the close of the last 5-minute bar that ENDS at or before `public_at`;
* the REACTION bar is the first 5-minute bar that STARTS at or after `public_at` + 5 minutes (a
  dissemination buffer: a bar that began before the release can never be the reaction bar), and no
  more than 10 minutes after that (else the event is skipped as a hole in the bars);
* m = reaction close / anchor close - 1; if |m| >= theta, `continuation` trades with the sign of m
  and `fade` against it, entering through the shared `IntradayScan` order path.

Everything a decision reads is known at the reaction bar's close. Not parity-proven against an
engine strategy, so its screens are advisory.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
from emporos.research.scans.event_days import InstrumentSymbols
from emporos.research.screen_trades import ScreenTrade

__all__ = [
    "BUFFER",
    "MAX_LATE",
    "Direction",
    "InSessionOutcome",
    "InSessionParameters",
    "InSessionResultsRules",
    "InSessionResultsScan",
    "declared_arms",
    "in_window",
]

WINDOW_OPEN, WINDOW_CLOSE = time(9, 15), time(14, 30)
BUFFER = timedelta(minutes=5)  # dissemination buffer before the reaction bar may start
MAX_LATE = timedelta(minutes=10)  # a reaction bar later than this after the buffer is a hole
_PERCENT = Decimal(100)


class Direction(StrEnum):
    CONTINUATION = "continuation"
    FADE = "fade"


@dataclass(frozen=True)
class InSessionParameters:
    theta_pct: Decimal
    direction: Direction

    def __post_init__(self) -> None:
        if self.theta_pct <= 0:
            raise ValueError("theta must be positive")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> InSessionParameters:
        return cls(Decimal(point["theta_pct"]), Direction(point["direction"]))

    def as_point(self) -> dict[str, str]:
        return {"theta_pct": str(self.theta_pct), "direction": self.direction.value}


def declared_arms(grid: Mapping[str, tuple[str, ...]]) -> list[InSessionParameters]:
    """Every arm of the declared grid. The declaration must name exactly this cell's parameters."""
    expected = ["theta_pct", "direction"]
    if sorted(grid) != sorted(expected):
        raise ValueError(f"the declared grid must name exactly {expected}, not {sorted(grid)}")
    points = product(*(grid[name] for name in expected))
    return [InSessionParameters.from_point(dict(zip(expected, p, strict=True))) for p in points]


def in_window(moment: datetime) -> bool:
    """A weekday publication time between 09:15 and 14:30 IST inclusive."""
    local = moment.astimezone(IST)
    return local.weekday() < 5 and WINDOW_OPEN <= local.time() <= WINDOW_CLOSE


@dataclass
class InSessionOutcome:
    """What the events did, for the report: a missing event is a missed trade, and the reader is
    told how many were missed and why."""

    measured_by_year: Counter[int] = field(default_factory=Counter)
    outside_window: int = 0
    no_session_bars: int = 0
    no_anchor: int = 0
    no_reaction_bar: int = 0
    long_signals: int = 0
    short_signals: int = 0


@dataclass
class _Event:
    published: datetime
    resolved: bool = False


class InSessionResultsRules:
    """`ScanRules` for one instrument that trade the reaction bar of each in-session event."""

    def __init__(
        self,
        parameters: InSessionParameters,
        events_by_day: Mapping[date, Sequence[datetime]],
        no_new_entries_after: time,
        outcome: InSessionOutcome,
    ) -> None:
        self._p = parameters
        self._events = {
            day: [_Event(e) for e in sorted(times)] for day, times in events_by_day.items()
        }
        self._cutoff = no_new_entries_after
        self._outcome = outcome
        self._day: date | None = None
        self._seen: list[Candle] = []  # today's bars before the current one

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        start = bar.ts.astimezone(IST)
        if start.date() != self._day:
            self._day, self._seen = start.date(), []
        intent = self._decide(bar, start, live=live, held=held, can_afford=can_afford)
        self._seen.append(bar)
        return intent

    def _decide(
        self, bar: Candle, start: datetime, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        signal: OrderSide | None = None
        for event in self._events.get(start.date(), ()):
            published = event.published.astimezone(IST)
            if event.resolved or start < published + BUFFER:
                continue
            event.resolved = True
            if start > published + BUFFER + MAX_LATE:
                self._outcome.no_reaction_bar += 1
                continue
            anchor = self._anchor(published)
            if anchor is None:
                self._outcome.no_anchor += 1
                continue
            move = bar.close.amount / anchor - 1
            self._outcome.measured_by_year[start.year] += 1
            if signal is None and abs(move) * _PERCENT >= self._p.theta_pct:
                signal = self._side(move)
        if signal is None or held is not None or not live or not can_afford:
            return None
        if not entries_open(bar, self._cutoff):
            return None
        if signal is OrderSide.BUY:
            self._outcome.long_signals += 1
        else:
            self._outcome.short_signals += 1
        return EntryIntent(signal)

    def _anchor(self, published: datetime) -> Decimal | None:
        """The close of the last bar that ended at or before the release."""
        before = [b for b in self._seen if b.closes_at.astimezone(IST) <= published]
        return before[-1].close.amount if before else None

    def _side(self, move: Decimal) -> OrderSide:
        with_the_news = OrderSide.BUY if move > 0 else OrderSide.SELL
        if self._p.direction is Direction.CONTINUATION:
            return with_the_news
        return OrderSide.SELL if with_the_news is OrderSide.BUY else OrderSide.BUY


class InSessionResultsScan:
    """A `SignalScan`: per instrument, the in-session results events of its symbol."""

    name = "in_session_results_drift"

    def __init__(
        self,
        parameters: InSessionParameters,
        execution: ScanExecution,
        events: Mapping[str, Sequence[datetime]],
        symbols: InstrumentSymbols,
    ) -> None:
        self._parameters = parameters
        self._execution = execution
        self._events = events
        self._symbols = symbols
        self.outcome = InSessionOutcome()

    def scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        sessions = {b.ts.astimezone(IST).date() for b in bars}
        if not sessions:
            return []
        first, last = min(sessions), max(sessions)
        by_day: dict[date, list[datetime]] = {}
        for published in self._events.get(self._symbols.symbol(instrument_id), ()):
            day = published.astimezone(IST).date()
            if not first <= day <= last:
                continue
            if not in_window(published):
                self.outcome.outside_window += 1
            elif day not in sessions:
                self.outcome.no_session_bars += 1
            else:
                by_day.setdefault(day, []).append(published)
        cutoff = self._execution.no_new_entries_after
        return IntradayScan(
            self.name,
            lambda: InSessionResultsRules(self._parameters, by_day, cutoff, self.outcome),
            self._execution,
        ).scan(instrument_id, bars)
