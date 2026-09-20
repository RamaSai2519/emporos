"""The backtest's market-data feed (plan.md §10): an iterator over CLOSED bars, never a frame.

    CandleRepository ──▶ ClosedBarFeed ──▶ (one bar at a time, in close order) ──▶ the run

Look-ahead is prevented by the shape of the thing, not by discipline:

* the feed is a single-pass async iterator. It has no length, no index, no `bars` attribute and
  cannot be rewound, so nothing holding it can ask for "the bar at t+3";
* it reads the repository lazily, one chunk of days at a time, so bars beyond the chunk being
  served are not even in memory while a strategy runs;
* it serves only bars that had closed by the end of the window, and only what `CandleReader` (the
  `CandleRepository` Protocol) returns for the requested range: the hot/cold split stays hidden.

Indicator warm-up is separate (`WarmupLoader`): a run may prime the strategy's history with bars
from BEFORE its start, which are past data by construction and produce no signals of their own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candles import CandleReader

DEFAULT_CHUNK = timedelta(days=30)


class FeedError(RuntimeError):
    """The repository handed back data the feed cannot serve honestly."""


class FeedConsumedError(FeedError):
    """A feed is one pass over the data; a second pass would be a second, different run."""


def _require_utc(name: str, when: datetime) -> None:
    if when.tzinfo is None or when.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True)
class FeedWindow:
    """`start <= bar.ts` and the bar closed by `end`."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_utc("window start", self.start)
        _require_utc("window end", self.end)
        if self.start >= self.end:
            raise ValueError("a feed window must start before it ends")


class ClosedBarFeed:
    def __init__(
        self,
        reader: CandleReader,
        instrument_ids: Sequence[str],
        timeframe: Timeframe,
        window: FeedWindow,
        chunk: timedelta = DEFAULT_CHUNK,
    ) -> None:
        if not instrument_ids or len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("a feed needs at least one instrument, each named once")
        if chunk <= timedelta(0):
            raise ValueError("the chunk must be a positive span")
        self._reader = reader
        self._instrument_ids = tuple(instrument_ids)
        self._rank = {instrument_id: i for i, instrument_id in enumerate(self._instrument_ids)}
        self._timeframe = timeframe
        self._window = window
        self._chunk = chunk
        self._consumed = False

    def __aiter__(self) -> AsyncIterator[Candle]:
        if self._consumed:
            raise FeedConsumedError("this feed has already been read; build a new one for a rerun")
        self._consumed = True
        return self._bars()

    async def _bars(self) -> AsyncIterator[Candle]:
        cursor = self._window.start
        while cursor < self._window.end:
            stop = min(cursor + self._chunk, self._window.end)
            for bar in await self._closed_bars(cursor, stop):
                yield bar
            cursor = stop

    async def _closed_bars(self, start: datetime, stop: datetime) -> list[Candle]:
        """Every instrument's bars for [start, stop), in close order; one instrument at a time."""
        bars: list[Candle] = []
        for instrument_id in self._instrument_ids:
            received = await self._reader.get_range(instrument_id, self._timeframe, start, stop)
            self._check(instrument_id, received, start, stop)
            bars += [bar for bar in received if bar.closes_at <= self._window.end]
        return sorted(bars, key=lambda bar: (bar.ts, self._rank[bar.instrument_id]))

    def _check(
        self, instrument_id: str, bars: Sequence[Candle], start: datetime, stop: datetime
    ) -> None:
        seen: set[datetime] = set()
        for bar in bars:
            if bar.instrument_id != instrument_id or bar.timeframe is not self._timeframe:
                raise FeedError(
                    f"asked for {instrument_id} {self._timeframe} bars, "
                    f"got {bar.instrument_id} {bar.timeframe}"
                )
            if not start <= bar.ts < stop:
                raise FeedError(
                    f"{instrument_id} bar {bar.ts.isoformat()} is outside the range read"
                )
            if bar.ts in seen:
                raise FeedError(f"{instrument_id} has two bars at {bar.ts.isoformat()}")
            seen.add(bar.ts)


class WarmupLoader:
    """The last `bars` closed bars per instrument before a moment, for priming strategy history."""

    def __init__(
        self,
        reader: CandleReader,
        instrument_ids: Sequence[str],
        timeframe: Timeframe,
        bars: int,
        lookback: timedelta,
    ) -> None:
        if bars < 0:
            raise ValueError("the warm-up depth cannot be negative")
        if lookback <= timedelta(0):
            raise ValueError("the warm-up lookback must be a positive span")
        self._reader = reader
        self._instrument_ids = tuple(instrument_ids)
        self._timeframe = timeframe
        self._bars = bars
        self._lookback = lookback

    async def load(self, before: datetime) -> tuple[Candle, ...]:
        """Bars that had closed by `before`, oldest first. Nothing at or after it, ever."""
        _require_utc("warm-up moment", before)
        if self._bars == 0:
            return ()
        warm: list[Candle] = []
        for instrument_id in self._instrument_ids:
            received = await self._reader.get_range(
                instrument_id, self._timeframe, before - self._lookback, before
            )
            closed = [bar for bar in received if bar.closes_at <= before]
            warm += sorted(closed, key=lambda bar: bar.ts)[-self._bars :]
        return tuple(sorted(warm, key=lambda bar: (bar.ts, bar.instrument_id)))
