"""EM-239: the FRED cues (parsing, as-of rule, transforms) and their polite, ledgered collection."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from emporos.research.filings.collector import CollectionHalted
from emporos.research.filings.polite import PoliteGet, SourceRefused
from emporos.research.filings.raw_store import FetchLedger
from emporos.research.market_context.global_cues import (
    CUES,
    CueCollector,
    CueSeries,
    GlobalCues,
    parse_fred_csv,
)

FIRST, LAST = date(2024, 1, 1), date(2026, 3, 18)
CSV = (
    "observation_date,SP500\n2024-01-02,4742.83\n2024-01-03,4704.81\n"
    "2024-01-04,.\n2024-01-05,4697.24\n"
)


class NoSleep:
    async def sleep(self, seconds: float) -> None:
        return None


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 26, 6, 0, tzinfo=UTC)


class TestParse:
    def test_missing_values_and_a_bad_row_are_dropped(self) -> None:
        text = CSV + "not-a-date,1\n2024-01-08\n"
        assert parse_fred_csv(text) == [
            (date(2024, 1, 2), 4742.83),
            (date(2024, 1, 3), 4704.81),
            (date(2024, 1, 5), 4697.24),
        ]

    def test_the_old_header_and_the_window_are_respected(self) -> None:
        text = "DATE,SP500\n2023-12-29,4769.83\n2024-01-02,4742.83\n2026-03-19,7000\n"
        assert parse_fred_csv(text, FIRST, LAST) == [(date(2024, 1, 2), 4742.83)]


class TestAsOf:
    def cues(self) -> GlobalCues:
        rows = [(date(2024, 1, 2), 100.0), (date(2024, 1, 3), 102.0), (date(2024, 1, 5), 99.96)]
        return GlobalCues({"SP500": CueSeries(rows)})

    def test_only_observations_dated_before_the_indian_day_count(self) -> None:
        assert self.cues().lines(date(2024, 1, 4)) == {"sp500_prev_close_pct": pytest.approx(2.0)}
        # An observation dated the day itself closes after the morning: not known.
        assert self.cues().lines(date(2024, 1, 3)) == {}

    def test_a_monday_reads_the_friday_close(self) -> None:
        got = self.cues().lines(date(2024, 1, 8))
        assert got["sp500_prev_close_pct"] == pytest.approx((99.96 / 102 - 1) * 100)

    def test_the_first_observation_has_nothing_to_move_from(self) -> None:
        assert self.cues().lines(date(2024, 1, 3)) == {}

    def test_a_stale_series_is_left_out(self) -> None:
        assert self.cues().lines(date(2024, 1, 20)) == {}

    def test_a_yield_is_a_level_and_basis_points(self) -> None:
        series = {"DGS10": CueSeries([(date(2024, 1, 2), 3.95), (date(2024, 1, 3), 4.0)])}
        got = GlobalCues(series).lines(date(2024, 1, 4))
        assert got == {"us10y_level": 4.0, "us10y_change_bp": pytest.approx(5.0)}

    def test_all_five_series_have_a_key_prefix(self) -> None:
        assert [c.prefix for c in CUES] == ["sp500", "nasdaq", "usdinr", "brent", "us10y"]

    def test_load_reads_the_kept_replies_and_tolerates_a_missing_one(self, tmp_path: Path) -> None:
        (tmp_path / "SP500.csv").write_text(CSV, encoding="utf-8")
        got = GlobalCues.load(tmp_path, FIRST, LAST).lines(date(2024, 1, 4))
        assert list(got) == ["sp500_prev_close_pct"]


def getter(handler) -> tuple[httpx.AsyncClient, PoliteGet]:  # type: ignore[no-untyped-def]
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return http, PoliteGet(http, NoSleep(), 3.0)


class TestCollector:
    async def test_it_keeps_the_reply_and_the_ledger_and_resumes(self, tmp_path: Path) -> None:
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(str(request.url))
            return httpx.Response(200, content=CSV.encode())

        ledger = FetchLedger(tmp_path / "ledger.jsonl")
        http, get = getter(handler)
        async with http:
            collector = CueCollector(get, tmp_path / "raw", ledger, FixedClock())
            assert await collector.run(CUES[:2], FIRST, LAST, lambda _: None) == []
            await collector.run(CUES[:2], FIRST, LAST, lambda _: None)
        assert len(asked) == 2  # the second run held both
        assert "id=SP500&cosd=2024-01-01&coed=2026-03-18" in asked[0]
        assert (tmp_path / "raw" / "SP500.csv").read_text(encoding="utf-8") == CSV
        first = ledger.records()[0]
        assert (first.symbol, first.count, first.url) == ("SP500", 3, asked[0])

    async def test_a_refusal_stops_the_run(self, tmp_path: Path) -> None:
        http, get = getter(lambda request: httpx.Response(429))
        async with http:
            collector = CueCollector(
                get, tmp_path / "raw", FetchLedger(tmp_path / "l"), FixedClock()
            )
            with pytest.raises(SourceRefused):
                await collector.run(CUES, FIRST, LAST, lambda _: None)

    async def test_three_failures_in_a_row_halt(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("no answer", request=request)

        http, get = getter(handler)
        async with http:
            collector = CueCollector(
                get, tmp_path / "raw", FetchLedger(tmp_path / "l"), FixedClock()
            )
            with pytest.raises(CollectionHalted):
                await collector.run(CUES, FIRST, LAST, lambda _: None)

    async def test_a_failure_then_success_is_listed_not_halting(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500)
            return httpx.Response(200, content=CSV.encode())

        http, get = getter(handler)
        async with http:
            collector = CueCollector(
                get, tmp_path / "raw", FetchLedger(tmp_path / "l"), FixedClock()
            )
            failed = await collector.run(CUES[:2], FIRST, LAST, lambda _: None)
        assert len(failed) == 1 and failed[0].startswith("SP500")
