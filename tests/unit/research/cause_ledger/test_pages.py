"""EM-244: the page collector: polite, ledgered, resumable, and refetching what a wipe removed."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from emporos.core.clock import Sleeper
from emporos.research.cause_ledger.pages import PageCollector, PageSpec
from emporos.research.filings.polite import PoliteGet, SourceRefused
from emporos.research.filings.raw_store import FetchLedger


class NoSleep(Sleeper):
    async def sleep(self, seconds: float) -> None:
        return None


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 26, tzinfo=UTC)


def collector(tmp_path: Path, status: int = 200) -> tuple[PageCollector, list[str]]:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(status, content=b"<html>ok</html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        PageCollector(
            PoliteGet(client, NoSleep(), 1.0), tmp_path / "raw",
            FetchLedger(tmp_path / "ledger.jsonl"), FixedClock(),
        ),
        asked,
    )  # fmt: skip


PAGES = [
    PageSpec("fed", "fed/a.htm", "https://x.org/a"),
    PageSpec("fed", "fed/b.htm", "https://x.org/b"),
]


def test_pages_are_kept_ledgered_and_not_fetched_twice(tmp_path: Path) -> None:
    run, asked = collector(tmp_path)

    assert asyncio.run(run.run(PAGES, lambda _: None)) == []
    assert asyncio.run(run.run(PAGES, lambda _: None)) == []

    assert asked == ["https://x.org/a", "https://x.org/b"]
    assert (tmp_path / "raw" / "fed" / "a.htm").read_bytes() == b"<html>ok</html>"
    assert len(FetchLedger(tmp_path / "ledger.jsonl").records()) == 2


def test_a_wiped_reply_is_fetched_again_even_though_the_ledger_has_it(tmp_path: Path) -> None:
    run, asked = collector(tmp_path)
    asyncio.run(run.run(PAGES, lambda _: None))
    (tmp_path / "raw" / "fed" / "a.htm").unlink()

    asyncio.run(run.run(PAGES, lambda _: None))

    assert asked == ["https://x.org/a", "https://x.org/b", "https://x.org/a"]


def test_a_refusal_stops_the_run(tmp_path: Path) -> None:
    run, asked = collector(tmp_path, status=403)

    with pytest.raises(SourceRefused):
        asyncio.run(run.run(PAGES, lambda _: None))
    assert asked == ["https://x.org/a"]  # nothing after the refusal
