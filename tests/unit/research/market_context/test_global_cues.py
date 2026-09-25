"""EM-239: the FRED cues (parsing, as-of rule, transforms) and their polite, ledgered collection."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from emporos.research.filings.collector import CollectionHalted
from emporos.research.filings.polite import PoliteGet, SourceRefused
from emporos.research.filings.raw_store import FetchLedger
from emporos.research.market_context.global_cues import (
    CUES,
    FRED,
    YAHOO,
    CueCollector,
    CueSeries,
    GlobalCues,
    parse_fred_csv,
    parse_yahoo_chart,
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
        return GlobalCues({"sp500": CueSeries(rows)})

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
        series = {"us10y": CueSeries([(date(2024, 1, 2), 3.95), (date(2024, 1, 3), 4.0)])}
        got = GlobalCues(series).lines(date(2024, 1, 4))
        assert got == {"us10y_level": 4.0, "us10y_change_bp": pytest.approx(5.0)}

    def test_all_five_series_have_a_key_prefix(self) -> None:
        assert [c.prefix for c in CUES] == ["sp500", "nasdaq", "usdinr", "brent", "us10y"]

    def test_load_reads_the_kept_replies_and_tolerates_a_missing_one(self, tmp_path: Path) -> None:
        (tmp_path / "fred").mkdir()
        (tmp_path / "fred" / "SP500.csv").write_text(CSV, encoding="utf-8")
        got = GlobalCues.load(tmp_path, FIRST, LAST, FRED).lines(date(2024, 1, 4))
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
            collector = CueCollector(get, tmp_path / "raw", ledger, FixedClock(), FRED)
            assert await collector.run(CUES[:2], FIRST, LAST, lambda _: None) == []
            await collector.run(CUES[:2], FIRST, LAST, lambda _: None)
        assert len(asked) == 2  # the second run held both
        assert "id=SP500&cosd=2024-01-01&coed=2026-03-18" in asked[0]
        assert (tmp_path / "raw" / "fred" / "SP500.csv").read_text(encoding="utf-8") == CSV
        first = ledger.records()[0]
        assert (first.symbol, first.count, first.url) == ("SP500", 3, asked[0])

    async def test_a_refusal_stops_the_run(self, tmp_path: Path) -> None:
        http, get = getter(lambda request: httpx.Response(429))
        async with http:
            collector = CueCollector(
                get, tmp_path / "raw", FetchLedger(tmp_path / "l"), FixedClock(), FRED
            )
            with pytest.raises(SourceRefused):
                await collector.run(CUES, FIRST, LAST, lambda _: None)

    async def test_three_failures_in_a_row_halt(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("no answer", request=request)

        http, get = getter(handler)
        async with http:
            collector = CueCollector(
                get, tmp_path / "raw", FetchLedger(tmp_path / "l"), FixedClock(), FRED
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
                get, tmp_path / "raw", FetchLedger(tmp_path / "l"), FixedClock(), FRED
            )
            failed = await collector.run(CUES[:2], FIRST, LAST, lambda _: None)
        assert len(failed) == 1 and failed[0].startswith("SP500")


def chart(offset: int, stamps: list[int], closes: list[float | None]) -> str:
    return json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": {"gmtoffset": offset},
                        "timestamp": stamps,
                        "indicators": {"quote": [{"close": closes}]},
                    }
                ],
                "error": None,
            }
        }
    )


# 2024-01-02 14:30 UTC (the NYSE open) and 2024-01-03 14:30 UTC; New York is UTC-5 in January.
OPEN_JAN_2, OPEN_JAN_3 = 1704205800, 1704292200


class TestYahoo:
    def test_dates_are_exchange_local_and_null_closes_are_dropped(self) -> None:
        text = chart(-18000, [OPEN_JAN_2, OPEN_JAN_3, OPEN_JAN_3 + 86400], [4742.0, None, 4700.5])
        assert parse_yahoo_chart(text) == [(date(2024, 1, 2), 4742.0), (date(2024, 1, 4), 4700.5)]

    def test_a_consent_page_or_a_bad_reply_is_no_observations(self) -> None:
        assert parse_yahoo_chart("<html>consent</html>") == []
        assert parse_yahoo_chart('{"chart": {"result": null, "error": {"code": "x"}}}') == []

    def test_the_window_is_respected_and_the_url_asks_for_it(self) -> None:
        text = chart(-18000, [OPEN_JAN_2], [1.0])
        assert parse_yahoo_chart(text, date(2024, 1, 3), None) == []
        url = YAHOO.url("^GSPC", FIRST, LAST)
        assert url.startswith("https://query1.finance.yahoo.com/v8/finance/chart/^GSPC?period1=")
        assert "interval=1d" in url

    async def test_the_collector_keeps_the_reply_and_stops_on_a_page_that_is_no_data(
        self, tmp_path: Path
    ) -> None:
        good = chart(-18000, [OPEN_JAN_2, OPEN_JAN_3], [1.0, 2.0])
        replies = iter([good, "<html>please enable JavaScript</html>"])
        http, get = getter(lambda request: httpx.Response(200, content=next(replies).encode()))
        ledger = FetchLedger(tmp_path / "l.jsonl")
        async with http:
            collector = CueCollector(get, tmp_path / "raw", ledger, FixedClock(), YAHOO)
            with pytest.raises(SourceRefused):
                await collector.run(CUES, FIRST, LAST, lambda _: None)
        assert (tmp_path / "raw" / "yahoo" / "_GSPC.json").read_text(encoding="utf-8") == good
        assert [(r.source, r.symbol, r.count) for r in ledger.records()] == [("yahoo", "^GSPC", 2)]
