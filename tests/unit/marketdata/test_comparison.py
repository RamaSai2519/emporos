"""The tool that renders the Phase-5 acceptance verdict: our 1m candles vs the broker's."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.marketdata.comparison import CandleComparator

T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)


def bar(minute: int, o: str = "100.00", h: str = "100.50", low: str = "99.50", c: str = "100.20",
        volume: int = 1000, partial: bool = False) -> Candle:  # fmt: skip
    return Candle("NSE:1", Timeframe.M1, T0 + timedelta(minutes=minute), Money.of(o), Money.of(h),
                  Money.of(low), Money.of(c), volume, partial)  # fmt: skip


def test_identical_series_agree() -> None:
    series = [bar(m) for m in range(5)]
    report = CandleComparator().compare(series, series)
    assert report.agrees and report.matched == 5 and report.mismatches == ()


def test_prices_within_one_tick_and_volume_within_two_percent_agree() -> None:
    report = CandleComparator().compare(
        [bar(0, c="100.25", volume=1015)], [bar(0, c="100.20", volume=1000)]
    )
    assert report.agrees


def test_a_price_beyond_the_tolerance_is_reported_by_field() -> None:
    report = CandleComparator().compare([bar(0, h="101.00")], [bar(0, h="100.50")])
    assert not report.agrees
    assert [(m.field, m.ours, m.broker) for m in report.mismatches] == [
        ("high", "101.00", "100.50")
    ]


def test_a_volume_beyond_the_relative_tolerance_is_reported() -> None:
    report = CandleComparator().compare([bar(0, volume=1100)], [bar(0, volume=1000)])
    assert [m.field for m in report.mismatches] == ["volume"]


def test_missing_minutes_are_reported_in_the_right_direction() -> None:
    report = CandleComparator().compare([bar(0), bar(2)], [bar(0), bar(1)])
    assert report.missing_from_ours == (T0 + timedelta(minutes=1),)
    assert report.missing_from_broker == (T0 + timedelta(minutes=2),)
    assert not report.agrees


def test_extra_minutes_on_our_side_alone_do_not_fail_the_comparison() -> None:
    assert CandleComparator().compare([bar(0), bar(1)], [bar(0)]).agrees


def test_partial_minutes_are_skipped_not_counted_as_mismatches() -> None:
    report = CandleComparator().compare(
        [bar(0, h="151.00", c="150.00", partial=True), bar(1)], [bar(0), bar(1)]
    )
    assert report.agrees and report.partial_skipped == 1 and report.matched == 1
