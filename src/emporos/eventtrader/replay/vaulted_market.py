"""The replay's `MarketData` over 5-minute candles (EM-240): whatever the loader can read (in
production the vaulted cold archive, so sealed days cannot be read), adjusted for splits and bonuses
so a signal and a fill are on one price basis, cut off at the window's last day.

Daily bars are aggregated from the 5-minute bars, the session calendar comes from a reference name
that trades every session, and a name is loaded once, on first use."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.eventtrader.replay.market import DailyBar

__all__ = [
    "AdjustedBarLoader",
    "BarAdjuster",
    "BarLoader",
    "NoAdjustment",
    "ThreadedBarLoader",
    "VaultedMarket",
    "session_calendar",
]


class BarLoader(Protocol):
    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        """5-minute candles for the days first..last inclusive, oldest first."""
        ...


class BarAdjuster(Protocol):
    def adjust_bars(self, instrument_id: str, raw: Sequence[Candle]) -> Sequence[Candle]: ...


class NoAdjustment:
    def adjust_bars(self, instrument_id: str, raw: Sequence[Candle]) -> Sequence[Candle]:
        return raw


class ThreadedBarLoader:
    """Runs a blocking loader on a worker thread. The vaulted loader starts its own event loop,
    which is not allowed on the thread the replay's loop is running on."""

    def __init__(self, inner: BarLoader) -> None:
        self._inner = inner
        self._pool = ThreadPoolExecutor(max_workers=1)

    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        return self._pool.submit(self._inner.load, instrument_id, first, last).result()


class AdjustedBarLoader:
    """A loader whose bars are already adjusted: what the context builder and the replay's market
    share, so a signal and a fill are on one price basis."""

    def __init__(self, inner: BarLoader, adjuster: BarAdjuster) -> None:
        self._inner, self._adjuster = inner, adjuster

    def load(self, instrument_id: str, first: date, last: date) -> Sequence[Candle]:
        return self._adjuster.adjust_bars(
            instrument_id, self._inner.load(instrument_id, first, last)
        )


def session_calendar(
    loader: BarLoader, reference: str, first: date, last: date
) -> tuple[date, ...]:
    """The days the reference name printed bars: the exchange's sessions."""
    days = {c.ts.astimezone(IST).date() for c in loader.load(reference, first, last)}
    return tuple(sorted(days))


@dataclass(frozen=True)
class _Series:
    by_day: dict[date, tuple[Candle, ...]]
    daily: dict[date, DailyBar]
    closes: tuple[datetime, ...]
    bars: tuple[Candle, ...]


def _daily(day: date, bars: Sequence[Candle]) -> DailyBar:
    return DailyBar(
        day,
        bars[0].open.amount,
        max(b.high.amount for b in bars),
        min(b.low.amount for b in bars),
        bars[-1].close.amount,
        sum(b.volume for b in bars),
    )


class VaultedMarket:
    def __init__(
        self,
        loader: BarLoader,
        adjuster: BarAdjuster,
        sessions: Sequence[date],
        first: date,
        last: date,
    ) -> None:
        if first > last:
            raise ValueError("the window starts before it ends")
        self._loader, self._adjuster = loader, adjuster
        self._first, self._last = first, last
        self._sessions = tuple(sorted(d for d in sessions if first <= d <= last))
        self._session_set = frozenset(self._sessions)
        self._series: dict[str, _Series] = {}

    def _get(self, instrument_id: str) -> _Series:
        cached = self._series.get(instrument_id)
        if cached is None:
            cached = self._build(instrument_id)
            self._series[instrument_id] = cached
        return cached

    def _build(self, instrument_id: str) -> _Series:
        raw = self._loader.load(instrument_id, self._first, self._last)
        bars = tuple(sorted(self._adjuster.adjust_bars(instrument_id, raw), key=lambda b: b.ts))
        grouped: dict[date, list[Candle]] = defaultdict(list)
        for bar in bars:
            day = bar.ts.astimezone(IST).date()
            if day in self._session_set:
                grouped[day].append(bar)
        by_day = {d: tuple(v) for d, v in grouped.items()}
        kept = tuple(b for d in sorted(by_day) for b in by_day[d])
        return _Series(
            by_day, {d: _daily(d, v) for d, v in by_day.items()}, tuple(b.closes_at for b in kept),
            kept,
        )  # fmt: skip

    # --- MarketData ----------------------------------------------------------------------------
    def five_minute_bars(self, instrument_id: str, day: date) -> Sequence[Candle]:
        return self._get(instrument_id).by_day.get(day, ())

    def daily_bars(self, instrument_id: str, after: date, count: int) -> Sequence[DailyBar]:
        table = self._get(instrument_id).daily
        days = [d for d in self._sessions if d > after][:count]
        return [table[d] for d in days if d in table]

    def next_session(self, day: date) -> date | None:
        return next((d for d in self._sessions if d > day), None)

    def is_session(self, day: date) -> bool:
        return day in self._session_set

    def last_close(self, instrument_id: str, at: datetime) -> Decimal | None:
        series = self._get(instrument_id)
        index = bisect_right(series.closes, at)
        return series.bars[index - 1].close.amount if index else None
