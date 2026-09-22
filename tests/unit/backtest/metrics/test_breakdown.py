"""EM-119: result breakdowns by direction, instrument, time of day and market regime."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from emporos.backtest.metrics.breakdown import (
    RegimeTimeline,
    TradeGrouper,
    build_regime_timeline,
    direction_key,
    instrument_key,
    regime_key_factory,
    time_of_day_key,
)
from emporos.backtest.portfolio import TradeDirection
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.strategies.regime import MarketRegimeClassifier
from tests.support.backtest_metrics import trade
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0


class TestTradeGrouper:
    def test_groups_are_named_and_sorted(self) -> None:
        trades = [
            trade("10", 0, direction=TradeDirection.SHORT),
            trade("-5", 10, direction=TradeDirection.LONG),
            trade("20", 20, direction=TradeDirection.LONG),
        ]

        groups = TradeGrouper().group(trades, direction_key)

        assert list(groups) == ["LONG", "SHORT"]
        assert groups["LONG"].count == 2 and groups["LONG"].net_pnl.amount == 15
        assert groups["SHORT"].count == 1 and groups["SHORT"].net_pnl.amount == 10

    def test_an_empty_sequence_produces_no_groups(self) -> None:
        assert TradeGrouper().group([], direction_key) == {}

    def test_by_instrument(self) -> None:
        trades = [
            trade("10", 0, instrument_id="NSE:1"),
            trade("20", 10, instrument_id="NSE:2"),
            trade("30", 20, instrument_id="NSE:1"),
        ]

        groups = TradeGrouper().group(trades, instrument_key)

        assert list(groups) == ["NSE:1", "NSE:2"]
        assert groups["NSE:1"].count == 2 and groups["NSE:1"].net_pnl.amount == 40
        assert groups["NSE:2"].count == 1


class TestTimeOfDayKey:
    def test_t0_is_the_session_open_hour(self) -> None:
        # T0 is 09:15 IST exactly (tests/support/strategies.py)
        assert time_of_day_key(trade("1", 0)) == "09:15-10:00"

    def test_a_trade_an_hour_later_falls_in_the_next_bucket(self) -> None:
        later = replace(trade("1", 0), opened_at=T0 + timedelta(hours=1, minutes=30))
        assert time_of_day_key(later) == "10:00-11:00"

    def test_near_the_close_falls_in_the_last_bucket(self) -> None:
        near_close = replace(trade("1", 0), opened_at=T0 + timedelta(hours=6, minutes=5))
        assert time_of_day_key(near_close) == "15:00-15:30"  # 15:20 IST

    def test_outside_session_hours_is_its_own_bucket(self) -> None:
        outside = replace(trade("1", 0), opened_at=T0 - timedelta(hours=2))
        assert time_of_day_key(outside) == "outside-session"


class TestRegimeTimeline:
    def test_reads_the_prior_completed_day_not_the_trades_own_day(self) -> None:
        timeline = RegimeTimeline({date(2026, 1, 5): "trending", date(2026, 1, 6): "ranging"})
        # A trade opening intraday on Jan 6 sees Jan 5's regime: Jan 6's own daily bar has not
        # closed yet while the trade is still open on Jan 6.
        opened_on_jan_6 = datetime(2026, 1, 6, 5, 0, tzinfo=UTC)
        assert timeline.at(opened_on_jan_6) == "trending"

    def test_a_trade_after_the_last_classified_day_sees_it(self) -> None:
        timeline = RegimeTimeline({date(2026, 1, 5): "trending"})
        opened_on_jan_7 = datetime(2026, 1, 7, 5, 0, tzinfo=UTC)
        assert timeline.at(opened_on_jan_7) == "trending"

    def test_before_any_classified_day_is_unknown(self) -> None:
        timeline = RegimeTimeline({date(2026, 1, 5): "trending"})
        opened_on_jan_5 = datetime(2026, 1, 5, 5, 0, tzinfo=UTC)
        assert timeline.at(opened_on_jan_5) is None

    def test_an_empty_timeline_is_always_unknown(self) -> None:
        assert RegimeTimeline({}).at(datetime(2026, 1, 5, tzinfo=UTC)) is None


def _daily_bar(day: int, close: str, width: str) -> Candle:
    mid = Decimal(close)
    half = Decimal(width) / 2
    return Candle(
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.D1,
        ts=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day),
        open=Money(mid),
        high=Money(mid + half),
        low=Money(mid - half),
        close=Money(mid),
        volume=1000,
    )


class TestBuildRegimeTimeline:
    def test_dates_the_classifiers_own_causal_output(self) -> None:
        # The same steady climb verified bar-by-bar in test_regime.py: ready and TRENDING at the
        # 7th bar, nothing classified before it.
        closes = [Decimal("1000") + Decimal("0.4") * i for i in range(1, 8)]
        widths = ["1.0", "1.2", "1.0", "1.2", "1.0", "1.2", "1.0"]
        bars = [
            _daily_bar(day, str(c), w)
            for day, (c, w) in enumerate(zip(closes, widths, strict=True), start=1)
        ]
        classifier = MarketRegimeClassifier(trend_period=4, atr_period=3, vol_window=5)

        timeline = build_regime_timeline(bars, classifier)

        next_day = bars[-1].ts.date() + timedelta(days=1)
        seen = datetime(next_day.year, next_day.month, next_day.day, tzinfo=UTC)
        assert timeline.at(seen) == "trending"
        assert timeline.at(bars[0].ts) is None  # nothing classified yet this early

    def test_an_empty_series_yields_an_always_unknown_timeline(self) -> None:
        timeline = build_regime_timeline([])
        assert timeline.at(datetime(2026, 1, 5, tzinfo=UTC)) is None


class TestRegimeKeyFactory:
    def test_tags_a_trade_by_its_instruments_regime(self) -> None:
        # T0 is 2026-01-05 09:15 IST (tests/support/strategies.py)
        timelines = {INSTRUMENT: RegimeTimeline({date(2026, 1, 4): "trending"})}
        key = regime_key_factory(timelines)
        assert key(trade("10", 0, instrument_id=INSTRUMENT)) == "trending"

    def test_an_instrument_with_no_timeline_is_unknown(self) -> None:
        key = regime_key_factory({})
        assert key(trade("10", 0, instrument_id=OTHER_INSTRUMENT)) == "unknown"

    def test_a_trade_before_any_classified_day_is_unknown(self) -> None:
        timelines = {INSTRUMENT: RegimeTimeline({date(2026, 1, 6): "ranging"})}
        key = regime_key_factory(timelines)
        assert key(trade("10", 0, instrument_id=INSTRUMENT)) == "unknown"
