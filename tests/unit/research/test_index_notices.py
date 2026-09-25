"""EM-224: the notice listing, which notices are candidates, and the polite collector."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from emporos.research.index_notices import (
    IndexNoticeCollector,
    NoticeRefused,
    NoticeStore,
    NseIndicesSource,
    parse_listing,
    select_candidates,
)


def item(stamp: str, name: str, title: str) -> str:
    return (
        f'<div class="pressItem" data-date="{stamp}" hidden="true"><p>{stamp}</p>'
        f"<a href='/Press_Release/{name}.pdf' target=\"_blank\">{title}</a></div>\n"
    )


PAGE = "".join(
    [
        item(
            "Sep 15, 2026", "ind_prs15092026", "Replacements in indices w.e.f. September 30, 2026"
        ),
        item("Sep 04, 2026", "ind_prs04092026", "Replacement in Nifty India FPI 150 index"),
        item(
            "Jul 15, 2025", "ind_prs15072025", "Corporate Action Adjustment for X in Nifty indices"
        ),
        item("Dec 11, 2025", "ind_prs11122025", "Exclusion of SKF India Ltd. from Nifty Indices"),
        item("Dec 30, 2025", "ind_prs30122025", "Changes in Nifty Fixed Income indices"),
        item("Jan 03, 2017", "ind_prs03012017", "Change in Indices w.e.f. January 10, 2017."),
        item("Mar 01, 2013", "ind_prs01032013", "Change in Nifty Midcap 50 Index"),
        item(
            "May 20, 2025", "ind_prs20052025", "Inclusion in Nifty IPO w.e.f. May 26 &amp; 27, 2025"
        ),
    ]
)


class Sleeper:
    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestListing:
    def test_it_reads_date_url_and_unescaped_title_newest_first(self) -> None:
        refs = parse_listing(PAGE)

        assert refs[0].notice_date == date(2026, 9, 15)
        assert refs[
            0
        ].url == "https://www.niftyindices.com/Press_Release/ind_prs15092025.pdf".replace(
            "15092025", "15092026"
        )
        assert refs[0].name == "ind_prs15092026.pdf"
        assert [r.notice_date for r in refs] == sorted((r.notice_date for r in refs), reverse=True)
        assert any(r.title.endswith("May 26 & 27, 2025") for r in refs)

    def test_a_page_with_no_notices_is_an_empty_list(self) -> None:
        assert parse_listing("<html></html>") == []


class TestCandidates:
    def test_only_notices_that_can_change_a_constituent_list_are_chosen_oldest_first(self) -> None:
        chosen = select_candidates(parse_listing(PAGE))

        assert [c.notice_date for c in chosen] == [
            date(2017, 1, 3), date(2025, 12, 11), date(2026, 9, 15)
        ]  # fmt: skip

    @pytest.mark.parametrize(
        "title",
        [
            "Changes in Nifty Fixed Income indices",
            "Inclusion in Nifty IPO w.e.f. May 26",
            "Replacement in Nifty SME Emerge index",
            "Corporate Action Adjustment for X",
            "Replacement in Nifty India FPI 150 index",
            "Revision in criteria for Nifty indices",
        ],
    )
    def test_unrelated_indices_and_adjustments_are_not(self, title: str) -> None:
        page = (
            f"data-date=\"Jan 05, 2026\" <a href='/Press_Release/ind_prs05012026.pdf'>{title}</a>"
        )

        assert select_candidates(parse_listing(page)) == []

    def test_notices_before_the_window_are_not(self) -> None:
        assert all(
            c.notice_date >= date(2016, 9, 1) for c in select_candidates(parse_listing(PAGE))
        )


class TestSource:
    async def test_it_asks_with_an_honest_client_and_spaces_requests(self) -> None:
        seen: list[httpx.Request] = []
        sleeper = Sleeper()

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=b"%PDF")

        source = NseIndicesSource(client(handler), sleeper)
        await source.pdf("https://x/a.pdf")
        await source.pdf("https://x/b.pdf")

        assert "emporos-research" in seen[0].headers["user-agent"]
        assert sleeper.slept == [3.0]

    @pytest.mark.parametrize("status", [401, 403, 429])
    async def test_a_refusal_stops_the_run(self, status: int) -> None:
        source = NseIndicesSource(client(lambda r: httpx.Response(status)), Sleeper())

        with pytest.raises(NoticeRefused, match=str(status)):
            await source.pdf("https://x/a.pdf")

    def test_it_will_not_hammer(self) -> None:
        with pytest.raises(ValueError, match="a second apart"):
            NseIndicesSource(client(lambda r: httpx.Response(200)), Sleeper(), 0.2)


class TestCollector:
    def store(self, tmp_path: Path) -> NoticeStore:
        return NoticeStore(tmp_path / "pdfs", tmp_path / "provenance.jsonl")

    async def test_it_saves_each_pdf_once_with_its_source_and_hash(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(200, content=b"PDFBYTES")

        store = self.store(tmp_path)
        candidates = select_candidates(parse_listing(PAGE))
        collector = IndexNoticeCollector(
            NseIndicesSource(client(handler), Sleeper()), store, date(2026, 9, 25)
        )

        first = await collector.run(candidates)
        second = await collector.run(candidates)

        assert first == (3, [])
        assert second == (0, [])
        assert len(calls) == 3
        rows = [
            json.loads(line)
            for line in (tmp_path / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert {r["fetched_on"] for r in rows} == {"2026-09-25"}
        assert all(len(r["sha256"]) == 64 and r["bytes"] == 8 for r in rows)
        assert (tmp_path / "pdfs" / "ind_prs15092026.pdf").read_bytes() == b"PDFBYTES"

    async def test_a_refusal_propagates_and_earlier_files_stay_recorded(
        self, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403 if "2025" in request.url.path else 200, content=b"x")

        store = self.store(tmp_path)
        collector = IndexNoticeCollector(
            NseIndicesSource(client(handler), Sleeper()), store, date(2026, 9, 25)
        )

        with pytest.raises(NoticeRefused):
            await collector.run(select_candidates(parse_listing(PAGE)))

        assert store.collected() == {
            "https://www.niftyindices.com/Press_Release/ind_prs03012017.pdf"
        }

    async def test_a_server_error_on_one_file_is_reported_and_the_run_goes_on(
        self, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500 if "2017" in request.url.path else 200, content=b"x")

        collector = IndexNoticeCollector(
            NseIndicesSource(client(handler), Sleeper()), self.store(tmp_path), date(2026, 9, 25)
        )

        fetched, failed = await collector.run(select_candidates(parse_listing(PAGE)))

        assert fetched == 2
        assert failed == ["https://www.niftyindices.com/Press_Release/ind_prs03012017.pdf"]
