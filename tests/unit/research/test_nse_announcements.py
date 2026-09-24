"""EM-191 D5: the announcements source asks politely, once per name, and stops when refused."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from emporos.cli.results_commands import ResultsCollection
from emporos.core.clock import FixedClock
from emporos.research.nse_announcements import AnnouncementRefused, NseAnnouncementSource
from emporos.research.results_filings import FilingLedger

FIRST, LAST = date(2016, 10, 3), date(2026, 9, 18)
GOOD = [
    {"an_dt": "22-Apr-2024 19:01:25", "desc": "Financial Result Updates", "seq_id": "9",
     "symbol": "X", "sm_isin": "I", "attchmntText": "t", "attchmntFile": "f"},
]  # fmt: skip


class RecordingSleeper:
    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestSource:
    async def test_the_request_names_the_window_the_subject_and_an_honest_client(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=GOOD)

        source = NseAnnouncementSource(client(handler), RecordingSleeper())
        (filing,) = await source.results_filings("M&M", FIRST, LAST)

        request = seen[0]
        assert request.url.params["symbol"] == "M&M"
        assert request.url.params["from_date"] == "03-10-2016"
        assert request.url.params["to_date"] == "18-09-2026"
        assert request.url.params["subject"] == "Financial Result Updates"
        assert "emporos-research" in request.headers["user-agent"]
        assert filing.seq_id == "9"

    async def test_requests_are_spaced_but_the_first_is_not_delayed(self) -> None:
        sleeper = RecordingSleeper()
        source = NseAnnouncementSource(
            client(lambda r: httpx.Response(200, json=[])), sleeper, seconds_between_requests=3.0
        )
        for symbol in ("A", "B", "C"):
            await source.results_filings(symbol, FIRST, LAST)

        assert sleeper.slept == [3.0, 3.0]

    @pytest.mark.parametrize("status", [403, 429, 401])
    async def test_a_refusal_stops_the_run(self, status: int) -> None:
        source = NseAnnouncementSource(client(lambda r: httpx.Response(status)), RecordingSleeper())
        with pytest.raises(AnnouncementRefused):
            await source.results_filings("A", FIRST, LAST)

    def test_requests_cannot_be_hammered(self) -> None:
        with pytest.raises(ValueError, match="a second apart"):
            NseAnnouncementSource(client(lambda r: httpx.Response(200)), RecordingSleeper(), 0.5)


class TestCollection:
    def collection(self, tmp_path: Path, handler) -> tuple[ResultsCollection, FilingLedger]:  # type: ignore[no-untyped-def]
        ledger = FilingLedger(tmp_path / "f.jsonl", tmp_path / "m.jsonl")
        source = NseAnnouncementSource(client(handler), RecordingSleeper())
        clock = FixedClock(datetime(2026, 9, 24, 15, 0, tzinfo=UTC))
        return ResultsCollection(source, ledger, clock), ledger

    async def test_a_name_already_collected_is_not_asked_again(self, tmp_path: Path) -> None:
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.params["symbol"])
            return httpx.Response(200, json=[])

        collection, ledger = self.collection(tmp_path, handler)
        ledger.record("A", [], FIRST, LAST, datetime(2026, 9, 24, tzinfo=UTC))

        await collection.run(["A", "B"], FIRST, LAST)

        assert asked == ["B"]

    async def test_one_failing_name_does_not_stop_the_others(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params["symbol"] == "BAD":
                return httpx.Response(500)
            return httpx.Response(200, json=GOOD)

        collection, ledger = self.collection(tmp_path, handler)

        added, failed = await collection.run(["A", "BAD", "C"], FIRST, LAST)

        assert failed == ["BAD"]
        assert ledger.collected_symbols() == {"A", "C"}  # the failed name is retried next run
        assert added == 1  # the same sequence id is one filing however many names report it

    async def test_a_refusal_propagates_and_keeps_what_was_collected(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429 if request.url.params["symbol"] == "B" else 200, json=[])

        collection, ledger = self.collection(tmp_path, handler)

        with pytest.raises(AnnouncementRefused):
            await collection.run(["A", "B", "C"], FIRST, LAST)

        assert ledger.collected_symbols() == {"A"}
