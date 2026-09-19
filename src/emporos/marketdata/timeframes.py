"""Higher timeframes derived from CLOSED 1m bars only (EM-52, plan.md §7).

5m / 15m / 1h bars are never built from ticks, so they are consistent with the 1m series by
construction. Buckets align to the session open (09:15 IST): 5m and 15m coincide with clock
boundaries, and 1h runs 09:15-10:15 ... 15:15-15:30 (the last is a 15-minute bucket, because the
session ends there). One pure fold (`fold_bucket`) serves both the streaming `TimeframeDeriver`
and the batch `derive`, which recovery uses to rebuild higher bars after backfilling 1m gaps.

A derived bar is `partial` if any constituent was, or if constituent minutes are missing.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.marketdata.aggregator import CandleSubscriber
from emporos.marketdata.session import SessionWindow

_LOG = logging.getLogger(__name__)
_MINUTE = timedelta(minutes=1)
DERIVED_TIMEFRAMES = (Timeframe.M5, Timeframe.M15, Timeframe.H1)
_MINUTES = {Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.H1: 60}


class BucketRule:
    """Where a 1m bar belongs in a higher timeframe, and how many minutes a bucket should hold."""

    def __init__(self, window: SessionWindow | None = None) -> None:
        self._window = window or SessionWindow()

    def start(self, minute: datetime, timeframe: Timeframe) -> datetime:
        opened = self._window.open_at(minute.astimezone(IST).date())
        step = timedelta(minutes=_MINUTES[timeframe])
        return (opened + ((minute - opened) // step) * step).astimezone(UTC)  # candles are UTC

    def end(self, bucket_start: datetime, timeframe: Timeframe) -> datetime:
        session_end = self._window.close_at(bucket_start.astimezone(IST).date())
        return min(bucket_start + timedelta(minutes=_MINUTES[timeframe]), session_end).astimezone(
            UTC
        )

    def expected_minutes(self, bucket_start: datetime, timeframe: Timeframe) -> int:
        return (self.end(bucket_start, timeframe) - bucket_start) // _MINUTE


def fold_bucket(
    bars: Sequence[Candle], timeframe: Timeframe, bucket_start: datetime, expected: int
) -> Candle:
    """Combine one bucket's 1m bars (any order) into the higher-timeframe bar."""
    ordered = sorted(bars, key=lambda bar: bar.ts)
    return Candle(
        instrument_id=ordered[0].instrument_id,
        timeframe=timeframe,
        ts=bucket_start,
        open=ordered[0].open,
        high=max(bar.high for bar in ordered),
        low=min(bar.low for bar in ordered),
        close=ordered[-1].close,
        volume=sum(bar.volume for bar in ordered),
        partial=len(ordered) < expected or any(bar.partial for bar in ordered),
    )


def derive(
    minutes: Iterable[Candle],
    timeframes: Sequence[Timeframe] = DERIVED_TIMEFRAMES,
    window: SessionWindow | None = None,
) -> list[Candle]:
    """Pure batch derivation: every higher-timeframe bar implied by a set of 1m bars."""
    rule = BucketRule(window)
    buckets: dict[tuple[str, Timeframe, datetime], dict[datetime, Candle]] = defaultdict(dict)
    for bar in minutes:
        if bar.timeframe is not Timeframe.M1:
            raise ValueError("only 1m bars can be derived from")
        for timeframe in timeframes:
            key = (bar.instrument_id, timeframe, rule.start(bar.ts, timeframe))
            buckets[key][bar.ts] = bar  # a repeated minute overwrites: derivation is idempotent
    derived = [
        fold_bucket(list(bars.values()), timeframe, start, rule.expected_minutes(start, timeframe))
        for (_, timeframe, start), bars in buckets.items()
    ]
    return sorted(derived, key=lambda c: (c.instrument_id, c.timeframe.value, c.ts))


@dataclass
class _Bucket:
    start: datetime
    bars: dict[datetime, Candle] = field(default_factory=dict)


class TimeframeDeriver:
    """A `CandleSubscriber` for 1m bars that emits 5m / 15m / 1h bars as their buckets complete."""

    def __init__(
        self,
        timeframes: Sequence[Timeframe] = DERIVED_TIMEFRAMES,
        window: SessionWindow | None = None,
    ) -> None:
        self._timeframes = tuple(timeframes)
        self._rule = BucketRule(window)
        self._open: dict[tuple[str, Timeframe], _Bucket] = {}
        self._subscribers: list[CandleSubscriber] = []
        self._stale_minutes = 0

    def subscribe(self, subscriber: CandleSubscriber) -> None:
        self._subscribers.append(subscriber)

    @property
    def stale_minutes_ignored(self) -> int:
        """1m bars that arrived for a bucket already emitted; the batch `derive` rebuilds those."""
        return self._stale_minutes

    def on_candle(self, candle: Candle) -> None:
        if candle.timeframe is not Timeframe.M1:
            return
        for timeframe in self._timeframes:
            self._add(candle, timeframe)

    def _add(self, minute: Candle, timeframe: Timeframe) -> None:
        key = (minute.instrument_id, timeframe)
        start = self._rule.start(minute.ts, timeframe)
        current = self._open.get(key)
        if current is not None and start < current.start:
            self._stale_minutes += 1  # its bucket was already emitted
            return
        if current is not None and start > current.start:
            self._emit(key, current, timeframe)  # a later bucket began: the earlier one is over
            current = None
        if current is None:
            current = self._open[key] = _Bucket(start)
        current.bars[minute.ts] = minute
        if minute.ts + _MINUTE == self._rule.end(start, timeframe):
            self._emit(key, current, timeframe)  # the bucket's last minute just closed

    def _emit(self, key: tuple[str, Timeframe], bucket: _Bucket, timeframe: Timeframe) -> None:
        del self._open[key]
        expected = self._rule.expected_minutes(bucket.start, timeframe)
        candle = fold_bucket(list(bucket.bars.values()), timeframe, bucket.start, expected)
        for subscriber in self._subscribers:
            try:
                subscriber.on_candle(candle)
            except Exception:
                _LOG.exception("candle subscriber %s failed", type(subscriber).__name__)
