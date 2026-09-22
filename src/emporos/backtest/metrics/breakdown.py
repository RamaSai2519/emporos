"""Trade-level breakdowns (plan.md's per-instrument, per-regime, time-of-day and direction result
tables): the same `TradeStatistics` computed per group, by reusing `TradeAnalyzer` rather than a
parallel statistics implementation."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime

from emporos.backtest.metrics.trades import TradeAnalyzer, TradeStatistics
from emporos.backtest.portfolio import ClosedTrade
from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.strategies.regime import MarketRegimeClassifier

TradeKey = Callable[[ClosedTrade], str]


class TradeGrouper:
    """Splits closed trades into named groups and analyzes each with the same `TradeAnalyzer`."""

    def __init__(self, analyzer: TradeAnalyzer | None = None) -> None:
        self._analyzer = analyzer or TradeAnalyzer()

    def group(self, trades: Sequence[ClosedTrade], key: TradeKey) -> dict[str, TradeStatistics]:
        buckets: dict[str, list[ClosedTrade]] = {}
        for closed in trades:
            buckets.setdefault(key(closed), []).append(closed)
        return {name: self._analyzer.analyze(buckets[name]) for name in sorted(buckets)}

    def group_cross(
        self, trades: Sequence[ClosedTrade], outer: TradeKey, inner: TradeKey
    ) -> dict[str, dict[str, TradeStatistics]]:
        """Two-level split: bucket by `outer` first (e.g. direction), then by `inner` inside each
        (e.g. instrument). Nested so the outer side is never merged back together."""
        outer_buckets: dict[str, list[ClosedTrade]] = {}
        for closed in trades:
            outer_buckets.setdefault(outer(closed), []).append(closed)
        return {
            outer_name: self.group(outer_buckets[outer_name], inner)
            for outer_name in sorted(outer_buckets)
        }


def direction_key(trade: ClosedTrade) -> str:
    return trade.direction.value


def instrument_key(trade: ClosedTrade) -> str:
    return trade.instrument_id


_SESSION_HOURS = (
    "09:15-10:00", "10:00-11:00", "11:00-12:00", "12:00-13:00",
    "13:00-14:00", "14:00-15:00", "15:00-15:30",
)  # fmt: skip


def time_of_day_key(trade: ClosedTrade) -> str:
    """The IST hour a trade opened in, bucketed one per session hour. A timestamp outside
    09:00-16:00 IST should not occur in session data; it gets its own bucket rather than being
    silently folded into the nearest one."""
    hour = trade.opened_at.astimezone(IST).hour
    if 9 <= hour <= 15:
        return _SESSION_HOURS[hour - 9]
    return "outside-session"


class RegimeTimeline:
    """One instrument's regime, dated by the close of each daily bar it was built from.

    `at(ts)` reads the regime as of the most recently COMPLETE prior trading day — never `ts`'s
    own day, whose daily bar has not closed while an intraday trade opened on it is still open.
    That is what keeps a regime-tagged trade look-ahead safe."""

    def __init__(self, by_date: Mapping[date, str]) -> None:
        self._by_date = dict(by_date)
        self._dates = sorted(self._by_date)

    def at(self, ts: datetime) -> str | None:
        day = ts.astimezone(IST).date()
        index = bisect_left(self._dates, day)
        if index == 0:
            return None
        return self._by_date[self._dates[index - 1]]


def build_regime_timeline(
    daily_bars: Sequence[Candle], classifier: MarketRegimeClassifier | None = None
) -> RegimeTimeline:
    """Runs a `MarketRegimeClassifier` forward once over `daily_bars` (oldest first). Causal by
    construction: each bar's regime is read from the classifier before the next bar is fed in, so
    it depends only on bars recorded at or before it — never a later one."""
    clf = classifier or MarketRegimeClassifier()
    by_date: dict[date, str] = {}
    for bar in daily_bars:
        regime = clf.update(bar)
        if regime is not None:
            by_date[bar.ts.astimezone(IST).date()] = regime.value
    return RegimeTimeline(by_date)


_UNKNOWN_REGIME = "unknown"


def regime_key_factory(timelines: Mapping[str, RegimeTimeline]) -> TradeKey:
    """Builds a `TradeKey` reading each trade's regime from its instrument's timeline. A trade
    whose instrument has no timeline, or that opened before the classifier had warmed up, falls
    into its own `"unknown"` bucket rather than being silently dropped or mis-tagged."""

    def _key(trade: ClosedTrade) -> str:
        timeline = timelines.get(trade.instrument_id)
        if timeline is None:
            return _UNKNOWN_REGIME
        return timeline.at(trade.opened_at) or _UNKNOWN_REGIME

    return _key
