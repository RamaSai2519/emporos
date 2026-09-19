"""Bar history a strategy may read — bounded to what had closed at the context clock's time.

Look-ahead safety (plan.md §10) is structural here: the store may HOLD a bar that has not closed
yet (a replay that pre-loaded a range, a feed running ahead), but `bars()` cannot return it — the
clock decides, not the caller. Backtest, paper and live all read history the same way.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Protocol

from emporos.core.clock import Clock
from emporos.domain.candles import Candle, Timeframe

DEFAULT_MAX_BARS = 5000


class BarHistory(Protocol):
    """What a strategy may read."""

    def bars(self, instrument_id: str, timeframe: Timeframe, limit: int) -> tuple[Candle, ...]:
        """The most recent `limit` CLOSED bars, oldest first."""
        ...


class BarRecorder(Protocol):
    """What whoever feeds the strategy may write. Deliberately not reachable from the context."""

    def record(self, bar: Candle) -> None: ...


class ClosedBarHistory:
    """Implements both halves; hand the reader to the context and the writer to the runner."""

    def __init__(self, clock: Clock, max_bars: int = DEFAULT_MAX_BARS) -> None:
        if max_bars <= 0:
            raise ValueError("max_bars must be positive")
        self._clock = clock
        self._max_bars = max_bars
        self._series: dict[tuple[str, Timeframe], list[Candle]] = {}

    def record(self, bar: Candle) -> None:
        """Keep a bar in time order. A bar for a timestamp already held replaces it (repaired)."""
        series = self._series.setdefault((bar.instrument_id, bar.timeframe), [])
        index = bisect_left(series, bar.ts, key=lambda held: held.ts)
        if index < len(series) and series[index].ts == bar.ts:
            series[index] = bar
        else:
            series.insert(index, bar)
        del series[: max(0, len(series) - self._max_bars)]

    def bars(self, instrument_id: str, timeframe: Timeframe, limit: int) -> tuple[Candle, ...]:
        if limit <= 0:
            return ()
        now = self._clock.now()
        series = self._series.get((instrument_id, timeframe), [])
        closed = [bar for bar in series if bar.closes_at <= now]
        return tuple(closed[-limit:])
