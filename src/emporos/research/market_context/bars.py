"""Compact 5-minute bars with as-of access (PROFIT_PLAN §12.2, EM-239, L-D3).

`IntradayBars` holds one instrument's 5-minute bars as float arrays and answers only "as of an
instant": a bar counts once it has CLOSED (its start plus five minutes), and a query names the
decision time `at`, so a bar that closes after `at` cannot be reached through any method here. The
session a bar belongs to is its IST date. A session's close is the close of its last bar (the bar
at 15:25 ends the regular session; a session that stops earlier keeps its own last bar).

`BarLoader` is the seam to storage (the vault-guarded reader in production, a fake in tests)."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle

__all__ = ["BAR_LENGTH", "BarLoader", "BarSeriesCache", "IntradayBars", "SessionBars"]

BAR_LENGTH = timedelta(minutes=5)


@dataclass(frozen=True)
class SessionBars:
    """The completed bars of one session, oldest first."""

    day: date
    closes: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    volumes: tuple[float, ...]
    ends: tuple[datetime, ...]  # each bar's close instant

    def __len__(self) -> int:
        return len(self.closes)


class IntradayBars:
    def __init__(self, bars: Sequence[Candle]) -> None:
        ordered = sorted({b.ts: b for b in bars}.values(), key=lambda b: b.ts)
        self._starts = [b.ts.timestamp() for b in ordered]
        self._ends = [s + BAR_LENGTH.total_seconds() for s in self._starts]
        self._open = [float(b.open.amount) for b in ordered]
        self._high = [float(b.high.amount) for b in ordered]
        self._low = [float(b.low.amount) for b in ordered]
        self._close = [float(b.close.amount) for b in ordered]
        self._volume = [float(b.volume) for b in ordered]
        self._day = [b.ts.astimezone(IST).date() for b in ordered]
        self._first_of: dict[date, int] = {}
        for i, day in enumerate(self._day):
            self._first_of.setdefault(day, i)
        self._days = sorted(self._first_of)

    def __len__(self) -> int:
        return len(self._starts)

    def completed(self, at: datetime) -> int:
        """How many bars (from the start of the series) have closed by `at`."""
        return bisect_right(self._ends, at.timestamp())

    def session(self, day: date, at: datetime) -> SessionBars | None:
        """Session `day`'s bars that have closed by `at`, or None when none has."""
        first = self._first_of.get(day)
        if first is None:
            return None
        end = self.completed(at)
        last = first
        while last < end and last < len(self._day) and self._day[last] == day:
            last += 1
        if last == first:
            return None
        sl = slice(first, last)
        return SessionBars(
            day, tuple(self._close[sl]), tuple(self._high[sl]), tuple(self._low[sl]),
            tuple(self._volume[sl]),
            tuple(datetime.fromtimestamp(t, IST) for t in self._ends[sl]),
        )  # fmt: skip

    def sessions_before(self, day: date, count: int, at: datetime) -> list[SessionBars]:
        """The last `count` sessions strictly before `day` that had closed by `at`, oldest first."""
        days = self._days[
            max(0, bisect_left(self._days, day) - count) : bisect_left(self._days, day)
        ]
        return [s for d in days if (s := self.session(d, at)) is not None]

    def last_session_day_on_or_before(self, day: date, at: datetime) -> date | None:
        """The latest session on or before `day` with a bar closed by `at`."""
        for d in reversed(self._days[: bisect_right(self._days, day)]):
            if self.session(d, at) is not None:
                return d
        return None

    def price_at(self, at: datetime) -> float | None:
        """The close of the last bar that had closed by `at`, or None before the first bar."""
        n = self.completed(at)
        return self._close[n - 1] if n else None


class BarLoader(Protocol):
    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        """The instrument's 5-minute bars over [first, last] (IST days), oldest first."""
        ...


class BarSeriesCache:
    """Loads each instrument's bars once (over the declared window) and keeps the most recent
    `capacity` of them, so a replay over many names never holds every series at once."""

    def __init__(self, loader: BarLoader, first: date, last: date, capacity: int = 64) -> None:
        self._loader = loader
        self._first = first
        self._last = last
        self._capacity = capacity
        self._held: OrderedDict[str, IntradayBars] = OrderedDict()

    def bars(self, instrument_id: str) -> IntradayBars:
        if instrument_id in self._held:
            self._held.move_to_end(instrument_id)
            return self._held[instrument_id]
        series = IntradayBars(self._loader.load(instrument_id, self._first, self._last))
        self._held[instrument_id] = series
        if len(self._held) > self._capacity:
            self._held.popitem(last=False)
        return series
