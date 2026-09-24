"""EM-220: daily bars from 5m bars. Session OHLCV, partial sessions, regular hours, the store."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.research.test_shock_reversal import FRIDAY, INSTRUMENT, MONDAY, session

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.persistence.candle_cache import CandleCacheFiles, FileCandleReader
from emporos.research.daily_bars import (
    DailyBarBuilder,
    DailyBarStore,
    SessionAggregator,
)
from emporos.research.partition import CONFIRMATION, DISCOVERY, DataSplit

VAULT_FIRST_DAY = date(2026, 3, 19)  # docs/research/edge-search/vault.yaml


def full_day(day: date, close: str = "100") -> list[Candle]:
    return session(day, open_="100", at_hour="100", close=close)


class FakeSource:
    """A `BarSource` over fixed bars that records who was asked for."""

    def __init__(self, bars: dict[str, list[Candle]]) -> None:
        self._bars = bars
        self.asked: list[tuple[str, str]] = []

    def bars(self, instrument_id: str, split: DataSplit) -> Sequence[Candle]:
        self.asked.append((instrument_id, split.name))
        return [
            b for b in self._bars.get(instrument_id, [])
            if split.contains(b.ts.astimezone(IST).date())
        ]  # fmt: skip


class TestSessionAggregator:
    def test_a_full_session_is_its_first_open_extremes_last_close_and_summed_volume(self) -> None:
        series = SessionAggregator().aggregate(INSTRUMENT, full_day(FRIDAY, close="104"))

        (bar,) = series.bars
        assert bar.timeframe is Timeframe.D1
        assert (bar.open.amount, bar.close.amount) == (Decimal(100), Decimal(104))
        assert (bar.high.amount, bar.low.amount) == (Decimal(105), Decimal(99))
        assert bar.volume == 75 * 1_000_000
        assert not bar.partial
        assert series.partial_days == series.thin_edge_days == ()

    def test_the_bar_is_stamped_at_midnight_ist_like_the_brokers_daily_bars(self) -> None:
        (bar,) = SessionAggregator().aggregate(INSTRUMENT, full_day(FRIDAY)).bars

        assert bar.ts == datetime(2026, 1, 1, 18, 30, tzinfo=UTC)
        assert bar.ts.astimezone(IST).date() == FRIDAY

    def test_sessions_come_out_oldest_first_whatever_order_the_bars_arrive_in(self) -> None:
        bars = full_day(MONDAY) + full_day(FRIDAY)

        days = [b.ts.astimezone(IST).date() for b in SessionAggregator().aggregate("x", bars).bars]

        assert days == [FRIDAY, MONDAY]

    @pytest.mark.parametrize("missing", [0, -1])
    def test_a_session_with_no_trade_in_its_first_or_last_slot_is_thin_but_not_partial(
        self, missing: int
    ) -> None:
        bars = full_day(FRIDAY)
        del bars[missing]

        series = SessionAggregator().aggregate(INSTRUMENT, bars)

        assert not series.bars[0].partial
        assert series.thin_edge_days == (FRIDAY,)

    def test_the_open_is_the_first_trade_when_the_opening_slot_is_empty(self) -> None:
        bars = full_day(FRIDAY)
        del bars[0]

        (bar,) = SessionAggregator().aggregate(INSTRUMENT, bars).bars

        assert bar.open == bars[0].open

    def test_a_gap_in_the_middle_is_not_thin(self) -> None:
        bars = full_day(FRIDAY)
        del bars[30]

        series = SessionAggregator().aggregate(INSTRUMENT, bars)

        assert series.thin_edge_days == ()
        assert not series.bars[0].partial

    def test_a_partial_source_bar_makes_the_session_partial(self) -> None:
        bars = full_day(FRIDAY)
        bars[10] = replace(bars[10], partial=True)

        series = SessionAggregator().aggregate(INSTRUMENT, bars)

        assert series.bars[0].partial
        assert series.partial_days == (FRIDAY,)

    def test_bars_outside_regular_hours_are_dropped_and_counted_not_folded_in(self) -> None:
        evening = replace(
            full_day(FRIDAY)[0],
            ts=datetime(2026, 1, 2, 12, 45, tzinfo=UTC),  # 18:15 IST
            high=Money.of(Decimal(500)),
        )

        series = SessionAggregator().aggregate(INSTRUMENT, [*full_day(FRIDAY), evening])

        assert series.off_hours_dropped == 1
        assert series.bars[0].high.amount == Decimal(101)

    def test_an_evening_only_day_makes_no_daily_bar(self) -> None:
        evening = replace(full_day(FRIDAY)[0], ts=datetime(2026, 1, 2, 12, 45, tzinfo=UTC))

        series = SessionAggregator().aggregate(INSTRUMENT, [evening])

        assert series.bars == ()
        assert series.off_hours_dropped == 1

    def test_a_repeated_timestamp_counts_once(self) -> None:
        bars = full_day(FRIDAY)

        series = SessionAggregator().aggregate(INSTRUMENT, [*bars, bars[5]])

        assert series.bars[0].volume == 75 * 1_000_000

    def test_no_bars_no_series(self) -> None:
        assert SessionAggregator().aggregate(INSTRUMENT, []).bars == ()


class TestStore:
    def test_months_are_written_in_the_caches_layout_and_read_back_by_the_file_reader(
        self, tmp_path: Path
    ) -> None:
        files = CandleCacheFiles(tmp_path)
        bars = SessionAggregator().aggregate(INSTRUMENT, full_day(FRIDAY) + full_day(MONDAY)).bars

        written = DailyBarStore(files).write(INSTRUMENT, bars)

        assert written == 1
        assert files.path(INSTRUMENT, "1d", "2026-01").is_file()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        got = asyncio.run(
            FileCandleReader([files]).get_range(
                INSTRUMENT, Timeframe.D1, start, start + timedelta(days=30)
            )
        )
        assert got == list(bars)

    def test_a_bar_of_another_instrument_or_timeframe_is_refused(self, tmp_path: Path) -> None:
        store = DailyBarStore(CandleCacheFiles(tmp_path))

        with pytest.raises(ValueError, match="one instrument's daily bars"):
            store.write("NSE:2", full_day(FRIDAY))  # 5m bars of NSE:1

    def test_a_rebuild_replaces_the_month(self, tmp_path: Path) -> None:
        files = CandleCacheFiles(tmp_path)
        store = DailyBarStore(files)
        aggregate = SessionAggregator().aggregate
        store.write(INSTRUMENT, aggregate(INSTRUMENT, full_day(FRIDAY, close="101")).bars)
        store.write(INSTRUMENT, aggregate(INSTRUMENT, full_day(FRIDAY, close="103")).bars)

        start = datetime(2026, 1, 1, tzinfo=UTC)
        (got,) = asyncio.run(
            FileCandleReader([files]).get_range(
                INSTRUMENT, Timeframe.D1, start, start + timedelta(days=30)
            )
        )
        assert got.close.amount == Decimal(103)


class TestBuilder:
    def build(self, tmp_path: Path, source: FakeSource) -> DailyBarBuilder:
        return DailyBarBuilder(
            source, DailyBarStore(CandleCacheFiles(tmp_path)), (DISCOVERY, CONFIRMATION)
        )

    def test_it_reports_sessions_partials_thin_edges_and_the_span(self, tmp_path: Path) -> None:
        short = full_day(MONDAY)
        del short[-1]
        friday = full_day(FRIDAY)
        friday[3] = replace(friday[3], partial=True)
        source = FakeSource({INSTRUMENT: friday + short})

        report = self.build(tmp_path, source).build(INSTRUMENT)

        assert (report.sessions, report.partial_days, report.thin_edge_days) == (2, 1, 1)
        assert (report.first, report.last) == (FRIDAY, MONDAY)
        assert report.files_written == 1

    def test_sessions_of_both_splits_land_in_one_series(self, tmp_path: Path) -> None:
        in_discovery = full_day(date(2024, 12, 30))
        in_confirmation = full_day(date(2025, 1, 2))
        source = FakeSource({INSTRUMENT: in_discovery + in_confirmation})

        report = self.build(tmp_path, source).build(INSTRUMENT)

        assert report.sessions == 2
        assert [split for _, split in source.asked] == ["discovery", "confirmation"]

    def test_it_asks_only_for_the_names_it_is_given(self, tmp_path: Path) -> None:
        source = FakeSource({"NSE:1": full_day(FRIDAY), "NSE:9": full_day(FRIDAY)})

        self.build(tmp_path, source).build_all(["NSE:1"])

        assert {i for i, _ in source.asked} == {"NSE:1"}

    def test_a_name_with_no_bars_writes_nothing(self, tmp_path: Path) -> None:
        report = self.build(tmp_path, FakeSource({})).build("NSE:7")

        assert (report.sessions, report.files_written, report.first) == (0, 0, None)

    def test_it_needs_a_split_to_read(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="at least one split"):
            DailyBarBuilder(FakeSource({}), DailyBarStore(CandleCacheFiles(tmp_path)), ())

    def test_the_splits_it_is_built_with_end_before_the_vault_opens(self) -> None:
        assert CONFIRMATION.last < VAULT_FIRST_DAY
