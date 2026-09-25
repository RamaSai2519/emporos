"""EM-239: the polite getter, the raw store and ledger, the universe and the collector."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from emporos.research.filings.collector import (
    CollectionHalted,
    FilingCollector,
    yearly_windows,
)
from emporos.research.filings.nse_source import NseFilingSource
from emporos.research.filings.polite import USER_AGENT, NotFound, PoliteGet, SourceRefused
from emporos.research.filings.raw_store import FetchLedger, RawFilingStore
from emporos.research.filings.universe import FilingUniverse, fo_stock_symbols


class NoSleep:
    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 25, 14, 0, tzinfo=UTC)


def client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


ROW = {
    "an_dt": "02-Jan-2024 13:33:26", "exchdisstime": "02-Jan-2024 13:33:30", "attchmntFile": "",
    "attchmntText": "t", "desc": "Updates", "seq_id": "1", "sm_isin": "INE1", "sm_name": "X",
    "symbol": "X",
}  # fmt: skip


class TestPoliteGet:
    async def test_it_spaces_requests_and_identifies_itself(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["user-agent"])
            return httpx.Response(200, content=b"ok")

        sleeper = NoSleep()
        async with client(handler) as c:
            getter = PoliteGet(c, sleeper, 3.0)
            await getter.get("https://a/1")
            await getter.get("https://a/2")

        assert seen == [USER_AGENT, USER_AGENT]
        assert sleeper.slept == [3.0]  # nothing before the first request, then one gap

    @pytest.mark.parametrize("status", [401, 403, 429])
    async def test_a_refusal_stops_the_run(self, status: int) -> None:
        async with client(lambda r: httpx.Response(status)) as c:
            with pytest.raises(SourceRefused):
                await PoliteGet(c, NoSleep()).get("https://a/1")

    async def test_a_404_is_not_a_refusal(self) -> None:
        async with client(lambda r: httpx.Response(404)) as c:
            with pytest.raises(NotFound):
                await PoliteGet(c, NoSleep()).get("https://a/1")

    def test_a_gap_under_a_second_is_refused(self) -> None:
        with pytest.raises(ValueError, match="a second apart"):
            PoliteGet(httpx.AsyncClient(), NoSleep(), 0.5)


class TestStoreAndLedger:
    def test_a_reply_is_kept_verbatim_and_hashed(self, tmp_path: Path) -> None:
        raw = RawFilingStore(tmp_path)

        digest = raw.write("NSE", "M&M", date(2024, 1, 1), date(2024, 12, 31), b"[1]")

        assert raw.read("NSE", "M&M", date(2024, 1, 1), date(2024, 12, 31)) == b"[1]"
        assert len(digest) == 64
        assert "M_and_M" in str(raw.path("NSE", "M&M", date(2024, 1, 1), date(2024, 12, 31)))


class TestUniverse:
    LOTS = (
        "UNDERLYING ,SYMBOL ,SEP-26\n"
        "NIFTY 50 ,NIFTY ,65\n"
        "Derivatives on Individual Securities,Symbol ,SEP-26\n"
        "ABB INDIA LIMITED ,ABB ,125\n"
        "MAHINDRA & MAHINDRA ,M&M ,350\n"
        ",,\n"
    )

    def test_the_stock_list_is_what_sits_under_the_individual_securities_heading(self) -> None:
        assert fo_stock_symbols(self.LOTS) == ["ABB", "M&M"]

    def test_a_file_with_no_stocks_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no individual-security"):
            fo_stock_symbols("UNDERLYING ,SYMBOL\nNIFTY 50 ,NIFTY\n")

    def test_held_out_names_are_never_in_the_universe_even_if_they_are_f_and_o(
        self, tmp_path: Path
    ) -> None:
        fo = tmp_path / "lots.csv"
        fo.write_text(self.LOTS, encoding="utf-8")

        universe = FilingUniverse.load(
            {"TCS": "NSE:1"}, frozenset({"NSE:2"}), {"NSE:1": "TCS", "NSE:2": "ABB"}, fo
        )

        assert universe.symbols == ["M&M", "TCS"]  # ABB is held out; TCS is D1; M&M is F&O only
        assert universe.held_out == {"ABB"}


class Scripted:
    """A FilingSource that answers from a script keyed by (symbol, first year)."""

    source = "NSE"

    def __init__(self, answers: dict[tuple[str, int], object]) -> None:
        self._answers = answers
        self.asked: list[tuple[str, int]] = []

    def url_for(self, symbol: str, first: date, last: date) -> str:
        return f"https://x/{symbol}?{first}..{last}"

    async def fetch(self, symbol: str, first: date, last: date) -> bytes:
        self.asked.append((symbol, first.year))
        answer = self._answers[(symbol, first.year)]
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer).encode()


class TestYearlyWindows:
    def test_the_span_is_cut_at_year_ends(self) -> None:
        assert yearly_windows(date(2024, 1, 1), date(2026, 3, 18)) == [
            (date(2024, 1, 1), date(2024, 12, 31)),
            (date(2025, 1, 1), date(2025, 12, 31)),
            (date(2026, 1, 1), date(2026, 3, 18)),
        ]


class TestCollector:
    def collector(self, tmp_path: Path, source: Scripted) -> FilingCollector:
        return FilingCollector(
            source,
            RawFilingStore(tmp_path / "raw"),
            FetchLedger(tmp_path / "l.jsonl"),
            FixedClock(),
        )

    async def test_it_records_each_window_and_a_second_run_asks_for_nothing(
        self, tmp_path: Path
    ) -> None:
        windows = yearly_windows(date(2024, 1, 1), date(2025, 6, 30))
        source = Scripted({("X", 2024): [ROW], ("X", 2025): []})
        lines: list[str] = []

        first = await self.collector(tmp_path, source).run(["X"], windows, lines.append)
        again = await self.collector(tmp_path, source).run(["X"], windows, lines.append)

        assert (first.fetched, first.filings, first.skipped) == (2, 1, 0)
        assert (again.fetched, again.skipped) == (0, 2)
        assert source.asked == [("X", 2024), ("X", 2025)]  # the second run asked nobody
        (record, _) = FetchLedger(tmp_path / "l.jsonl").records()
        assert (record.symbol, record.count, record.url.startswith("https://x/X")) == ("X", 1, True)

    async def test_a_404_is_recorded_as_an_empty_window(self, tmp_path: Path) -> None:
        source = Scripted({("X", 2024): NotFound("x")})

        report = await self.collector(tmp_path, source).run(
            ["X"], [(date(2024, 1, 1), date(2024, 12, 31))], lambda _: None
        )

        assert (report.fetched, report.filings, report.failed) == (1, 0, [])

    async def test_a_failure_is_listed_and_left_for_a_retry(self, tmp_path: Path) -> None:
        source = Scripted({("X", 2024): ValueError("bad json"), ("Y", 2024): []})

        report = await self.collector(tmp_path, source).run(
            ["X", "Y"], [(date(2024, 1, 1), date(2024, 12, 31))], lambda _: None
        )

        assert report.failed == ["X 2024-01-01..2024-12-31"]
        assert report.fetched == 1

    async def test_three_failures_in_a_row_halt_the_run(self, tmp_path: Path) -> None:
        source = Scripted({(s, 2024): ValueError("html") for s in "ABC"})

        with pytest.raises(CollectionHalted):
            await self.collector(tmp_path, source).run(
                list("ABC"), [(date(2024, 1, 1), date(2024, 12, 31))], lambda _: None
            )

    async def test_a_refusal_propagates(self, tmp_path: Path) -> None:
        source = Scripted({("X", 2024): SourceRefused("403")})

        with pytest.raises(SourceRefused):
            await self.collector(tmp_path, source).run(
                ["X"], [(date(2024, 1, 1), date(2024, 12, 31))], lambda _: None
            )


class TestNseSource:
    def test_the_address_carries_the_symbol_and_the_window(self) -> None:
        source = NseFilingSource(PoliteGet(httpx.AsyncClient(), NoSleep()))

        url = source.url_for("M&M", date(2024, 1, 1), date(2024, 12, 31))

        assert "symbol=M%26M" in url
        assert "from_date=01-01-2024" in url and "to_date=31-12-2024" in url
