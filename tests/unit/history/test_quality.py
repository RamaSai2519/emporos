"""EM-132: each dataset check finds its own defect in a hand-made series and nothing else."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.history.quality import (
    DatasetAuditor,
    DuplicateTimestamps,
    IntradayGaps,
    MarketDays,
    MissingMarketDays,
    OvernightDiscontinuity,
    SeriesContext,
    Severity,
    TimestampAlignment,
)
from emporos.marketdata.session import SessionWindow

DAY1, DAY2, DAY3 = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)  # Mon, Tue, Wed
ID = "NSE:1"


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=IST).astimezone(UTC)


def bar(ts: datetime, open_: str = "100", close: str | None = None, instrument: str = ID) -> Candle:
    o, c = Decimal(open_), Decimal(close or open_)
    high, low = max(o, c), min(o, c)
    return Candle(instrument, Timeframe.M5, ts, Money(o), Money(high), Money(low), Money(c), 10)


def full_day(day: date, open_: str = "100", close: str | None = None, instrument: str = ID):  # type: ignore[no-untyped-def]
    slots = [at(day, 9, 15) + timedelta(minutes=5 * n) for n in range(75)]
    last = len(slots) - 1
    return [
        bar(ts, open_ if n == 0 else "100", close if n == last and close else None, instrument)
        for n, ts in enumerate(slots)
    ]


def context(days: tuple[date, ...] = (DAY1, DAY2, DAY3)) -> SeriesContext:
    return SeriesContext(Timeframe.M5, frozenset(days), SessionWindow())


def test_the_session_holds_seventy_five_five_minute_slots() -> None:
    assert context().slots_per_day == 75
    assert SeriesContext(Timeframe.M1, frozenset(), SessionWindow()).slots_per_day == 375


def test_a_duplicate_timestamp_is_an_error() -> None:
    bars = [*full_day(DAY1), bar(at(DAY1, 9, 15))]

    (finding,) = DuplicateTimestamps().run(ID, bars, context())

    assert finding.severity is Severity.ERROR and finding.day == DAY1


def test_a_clean_series_has_no_duplicates() -> None:
    assert DuplicateTimestamps().run(ID, full_day(DAY1), context()) == []


def test_bars_off_the_grid_or_outside_the_session_are_errors() -> None:
    misaligned = bar(at(DAY1, 9, 17))
    shifted = bar(at(DAY1, 9, 15) - timedelta(hours=5, minutes=30))  # the IST offset dropped
    before_open = bar(at(DAY1, 9, 10))
    after_close = bar(at(DAY1, 15, 30))

    found = TimestampAlignment().run(
        ID, [*full_day(DAY1), misaligned, shifted, before_open, after_close], context()
    )

    assert len(found) == 4 and all(f.severity is Severity.ERROR for f in found)


def test_a_full_day_is_aligned() -> None:
    assert TimestampAlignment().run(ID, full_day(DAY1), context()) == []


def test_a_market_day_the_instrument_lacks_is_reported_between_its_first_and_last_day() -> None:
    bars = [*full_day(DAY1), *full_day(DAY3)]

    (finding,) = MissingMarketDays().run(ID, bars, context())

    assert finding.day == DAY2 and finding.severity is Severity.WARNING


def test_days_before_the_first_bar_are_not_missing_they_are_before_the_listing() -> None:
    assert MissingMarketDays().run(ID, full_day(DAY2), context()) == []


def test_a_short_day_is_reported_with_how_many_bars_are_missing() -> None:
    short = full_day(DAY2)[:70]

    (finding,) = IntradayGaps().run(ID, [*full_day(DAY1), *short], context())

    assert finding.day == DAY2 and "70 of 75" in finding.detail and "5 missing" in finding.detail


def test_a_tolerance_forgives_a_day_missing_a_few_bars() -> None:
    assert IntradayGaps(tolerated_missing=5).run(ID, full_day(DAY2)[:70], context()) == []


def test_a_day_that_was_not_a_market_day_is_not_judged() -> None:
    assert IntradayGaps().run(ID, full_day(DAY2)[:10], context(days=(DAY1,))) == []


def test_a_halving_overnight_looks_like_a_split_and_a_small_move_does_not() -> None:
    split = [*full_day(DAY1, close="200"), *full_day(DAY2, open_="100")]
    calm = [*full_day(DAY1, close="100"), *full_day(DAY2, open_="103")]

    (found,) = OvernightDiscontinuity().run(ID, split, context())
    assert found.day == DAY2 and "0.5000" in found.detail
    assert OvernightDiscontinuity().run(ID, calm, context()) == []


def test_the_discontinuity_limit_is_a_setting() -> None:
    calm = [*full_day(DAY1, close="100"), *full_day(DAY2, open_="103")]

    assert len(OvernightDiscontinuity(limit=Decimal("0.02")).run(ID, calm, context())) == 1


def test_market_days_are_the_days_half_the_instruments_traded() -> None:
    series = {
        "A": [*full_day(DAY1, instrument="A"), *full_day(DAY2, instrument="A")],
        "B": full_day(DAY1, instrument="B"),
        "C": full_day(DAY1, instrument="C"),
        "D": full_day(DAY1, instrument="D"),
    }

    assert MarketDays().infer(series) == frozenset({DAY1})  # DAY2 had 1 of 4: a holiday-ish day
    assert MarketDays().infer({}) == frozenset()


class FakeReader:
    def __init__(self, series: dict[str, list[Candle]]) -> None:
        self._series = series

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        return [b for b in self._series[instrument_id] if start <= b.ts < end]


async def test_the_auditor_runs_every_check_over_every_instrument_and_reports_spans() -> None:
    series = {
        "A": [*full_day(DAY1, instrument="A"), *full_day(DAY2, instrument="A")],
        "B": [*full_day(DAY1, instrument="B"), *full_day(DAY2, close="200", instrument="B")[:60]],
    }

    report = await DatasetAuditor(FakeReader(series)).audit(["A", "B"], Timeframe.M5, DAY1, DAY2)

    assert report.market_days == 2 and report.errors == 0
    assert report.count("intraday_gaps") == 1  # B's short second day
    assert [(s.instrument_id, s.bars) for s in report.spans] == [("A", 150), ("B", 135)]
    assert report.spans[0].first_day == DAY1 and report.spans[0].last_day == DAY2


async def test_an_instrument_with_no_bars_has_an_empty_span_and_no_findings() -> None:
    report = await DatasetAuditor(FakeReader({"A": full_day(DAY1, instrument="A"), "Z": []})).audit(
        ["A", "Z"], Timeframe.M5, DAY1, DAY1
    )

    assert report.spans[1].bars == 0 and report.spans[1].first_day is None
    assert [f for f in report.findings if f.instrument_id == "Z"] == []


async def test_the_auditor_refuses_a_timeframe_it_has_no_grid_for() -> None:
    with pytest.raises(ValueError, match="1m, 5m and 15m"):
        await DatasetAuditor(FakeReader({})).audit([], Timeframe.H1, DAY1, DAY1)


async def test_the_report_renders_as_markdown_and_json_with_every_finding() -> None:
    from emporos.history.quality_report import QualityJson, QualityMarkdown

    series = {"A": [*full_day(DAY1, close="200", instrument="A"), *full_day(DAY2, instrument="A")]}
    report = await DatasetAuditor(FakeReader(series)).audit(["A"], Timeframe.M5, DAY1, DAY2)

    text = QualityMarkdown().render(report, "## Notes\n\nsurvivorship")
    record = QualityJson().of(report)

    assert "# History quality: 5m bars, 2026-03-02..2026-03-03" in text
    assert "| overnight_discontinuity |" in text and "## overnight_discontinuity (1)" in text
    assert text.rstrip().endswith("survivorship")
    assert record["errors"] == 0 and record["spans"][0]["bars"] == 150
    assert [f["check"] for f in record["findings"]] == ["overnight_discontinuity"]
