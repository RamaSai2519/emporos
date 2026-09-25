"""EM-233: ETF daily bars: windows, resolution, the fetch loop and the audit of what was stored."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
from tests.unit.research.swing.support import bar, sessions

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.research.etf_bars import (
    ETF_SYMBOLS,
    EtfBarAudit,
    EtfBarFetcher,
    EtfFetchReport,
    EtfResolver,
    fetch_windows,
)

ROWS = [
    {"exch_seg": "NSE", "symbol": "NIFTYBEES-EQ", "token": "10576", "name": "NIFTYBEES",
     "lotsize": "1", "tick_size": "1"},
    {"exch_seg": "NSE", "symbol": "JUNIORBEES-EQ", "token": "15332", "name": "JUNIORBEES",
     "lotsize": "1", "tick_size": "5"},
    {"exch_seg": "NSE", "symbol": "GOLDBEES-EQ", "token": "14428", "name": "GOLDBEES",
     "lotsize": "1", "tick_size": "1"},
    {"exch_seg": "BSE", "symbol": "NIFTYBEES-EQ", "token": "999", "name": "x", "lotsize": "1"},
]  # fmt: skip


class Source:
    """Serves daily bars from a fixed calendar, and records each window it was asked for."""

    def __init__(self, days_by_symbol: dict[str, list[date]]) -> None:
        self._days = days_by_symbol
        self.asked: list[tuple[str, datetime, datetime]] = []

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.asked.append((instrument.tradingsymbol, start, end))
        return [
            bar(instrument.instrument_id, d, "100", "101")
            for d in self._days.get(instrument.tradingsymbol, [])
            if start <= datetime.combine(d, datetime.min.time(), tzinfo=IST) < end
        ]


class Archive:
    def __init__(self) -> None:
        self.candles: list[Candle] = []

    async def read(self, *args: object) -> list[Candle]:
        return []

    async def archive(self, candles: Iterable[Candle]) -> None:
        self.candles.extend(candles)


class TestWindows:
    def test_windows_cover_the_span_without_overlap_and_the_last_ends_after_the_last_day(
        self,
    ) -> None:
        windows = fetch_windows(date(2020, 1, 1), date(2021, 12, 31))

        assert windows[0][0].astimezone(IST).date() == date(2020, 1, 1)
        assert windows[-1][1].astimezone(IST).date() == date(2022, 1, 1)
        assert all(a[1] == b[0] for a, b in pairwise(windows))
        assert all(end - start <= timedelta(days=365) for start, end in windows)
        assert len(windows) == 3  # 366 + 365 days is 731: 365 + 365 + 1

    def test_a_single_day_is_one_window(self) -> None:
        assert len(fetch_windows(date(2020, 1, 1), date(2020, 1, 1))) == 1

    @pytest.mark.parametrize(
        ("first", "last", "days"),
        [(date(2020, 2, 1), date(2020, 1, 1), 365), (date(2020, 1, 1), date(2020, 2, 1), 0)],
    )
    def test_a_bad_span_is_refused(self, first: date, last: date, days: int) -> None:
        with pytest.raises(ValueError, match="positive window"):
            fetch_windows(first, last, days)


class TestResolver:
    def test_it_finds_the_three_nse_rows_and_reads_the_tick_in_paise(self) -> None:
        found = EtfResolver().resolve(ROWS)

        assert [i.tradingsymbol for i in found] == list(ETF_SYMBOLS)
        assert [i.instrument_id for i in found] == ["NSE:10576", "NSE:15332", "NSE:14428"]
        assert found[1].tick_size.amount == Decimal("0.05")
        assert found[0].tick_size.amount == Decimal("0.01")

    def test_a_symbol_that_is_missing_or_ambiguous_stops_the_run(self) -> None:
        with pytest.raises(ValueError, match="0 NSE rows"):
            EtfResolver().resolve(ROWS[:2])
        with pytest.raises(ValueError, match="2 NSE rows"):
            EtfResolver().resolve([*ROWS[:3], ROWS[0]])


class TestFetcher:
    async def test_it_fetches_every_window_for_every_etf_and_stores_the_bars(self) -> None:
        days = sessions(300, date(2020, 1, 1))
        source, archive = Source({"NIFTYBEES-EQ": days, "GOLDBEES-EQ": days[100:]}), Archive()

        reports = await EtfBarFetcher(source, archive).run(
            EtfResolver().resolve(ROWS), date(2019, 6, 1), date(2021, 6, 30)
        )

        by_symbol = {r.symbol: r for r in reports}
        assert by_symbol["NIFTYBEES-EQ"].bars == 300
        assert by_symbol["NIFTYBEES-EQ"].first_day == date(2020, 1, 1)
        assert by_symbol["GOLDBEES-EQ"].first_day == days[100]
        assert by_symbol["JUNIORBEES-EQ"].bars == 0
        assert by_symbol["JUNIORBEES-EQ"].first_day is None
        assert len(archive.candles) == 300 + 200
        assert len(source.asked) == 3 * by_symbol["NIFTYBEES-EQ"].windows

    async def test_a_full_year_window_that_comes_back_thin_is_flagged(self) -> None:
        days = sessions(120, date(2020, 1, 1))  # 120 sessions in a 365-day window: too few

        reports = await EtfBarFetcher(Source({"NIFTYBEES-EQ": days}), Archive()).run(
            EtfResolver().resolve(ROWS, ["NIFTYBEES-EQ"]), date(2020, 1, 1), date(2021, 12, 31)
        )

        (report,) = reports
        assert report.sparse_windows == ((date(2020, 1, 1), 120),)

    async def test_an_empty_window_is_not_flagged_it_is_before_the_listing(self) -> None:
        reports = await EtfBarFetcher(Source({}), Archive()).run(
            EtfResolver().resolve(ROWS, ["NIFTYBEES-EQ"]), date(2001, 1, 1), date(2003, 12, 31)
        )

        assert reports[0].sparse_windows == ()

    async def test_bars_outside_the_window_are_dropped(self) -> None:
        class Leaky(Source):
            async def fetch(self, instrument, timeframe, start, end):  # type: ignore[no-untyped-def]
                return [bar(instrument.instrument_id, date(2030, 1, 3), "1", "1")]

        archive = Archive()

        reports = await EtfBarFetcher(Leaky({}), archive).run(
            EtfResolver().resolve(ROWS, ["NIFTYBEES-EQ"]), date(2020, 1, 1), date(2020, 6, 1)
        )

        assert archive.candles == []
        assert reports[0].bars == 0


class Index:
    def __init__(self, moves: dict[date, str]) -> None:
        self._moves = moves

    def close_to_close(self, day: date) -> Decimal | None:
        return Decimal(self._moves[day]) if day in self._moves else None


class TestAudit:
    DAYS = sessions(30, date(2020, 1, 1))

    def report(self) -> EtfFetchReport:
        return EtfFetchReport("NSE:10576", "NIFTYBEES-EQ", 0, None, None, 1, ())

    def bars(self, skip: set[int] = frozenset(), split_at: int | None = None) -> list[Candle]:  # type: ignore[assignment]
        out = []
        for i, d in enumerate(self.DAYS):
            if i in skip:
                continue
            price = "100" if split_at is None or i < split_at else "10"
            out.append(bar("NSE:10576", d, price, price))
        return out

    def test_it_reports_the_span_bars_per_year_and_sessions_the_index_had_that_it_lacks(
        self,
    ) -> None:
        audit = EtfBarAudit(Index({}), self.DAYS)

        result = audit.audit(self.report(), self.bars(skip={5, 6}))

        assert result["first_day"] == self.DAYS[0].isoformat()
        assert result["bars"] == 28
        assert result["bars_per_year"] == {2020: 28}
        assert result["gaps_against_nifty"] == [self.DAYS[5].isoformat(), self.DAYS[6].isoformat()]
        assert result["discontinuities"] == []

    def test_sessions_before_the_etfs_first_bar_are_not_gaps(self) -> None:
        audit = EtfBarAudit(Index({}), self.DAYS)
        late = [b for b in self.bars() if b.ts.astimezone(IST).date() >= self.DAYS[10]]

        assert audit.audit(self.report(), late)["gaps_against_nifty"] == []

    def test_a_unit_split_is_a_split_shaped_artifact_with_its_reason(self) -> None:
        audit = EtfBarAudit(Index({}), self.DAYS)

        result = audit.audit(self.report(), self.bars(split_at=15))

        (row,) = result["discontinuities"]
        assert row["day"] == self.DAYS[15].isoformat()
        assert row["class"] == "artifact"
        assert "split-shaped" in row["reason"]
        assert result["gap_classes"] == {"artifact": 1}

    def test_a_gap_the_market_made_is_real(self) -> None:
        crash = self.DAYS[15]
        audit = EtfBarAudit(Index({crash: "-0.13"}), self.DAYS)
        bars = [bar("NSE:10576", d, "100" if i < 15 else "80", "100" if i < 15 else "80")
                for i, d in enumerate(self.DAYS)]  # fmt: skip

        result = audit.audit(self.report(), bars)

        assert result["discontinuities"][0]["class"] == "real"

    def test_no_bars_no_span(self) -> None:
        result = EtfBarAudit(Index({}), self.DAYS).audit(self.report(), [])

        assert (result["first_day"], result["last_day"], result["bars"]) == (None, None, 0)
