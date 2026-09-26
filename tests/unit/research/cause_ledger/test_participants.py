"""EM-244: participant-wise F&O files: parsing, positions, evening availability, holidays."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from emporos.core.clock import IST, Sleeper
from emporos.research.cause_ledger.pages import PageCollector
from emporos.research.cause_ledger.participants import (
    available_at,
    parse_participant_file,
    participant_pages,
)
from emporos.research.filings.polite import PoliteGet
from emporos.research.filings.raw_store import FetchLedger

DAY = date(2024, 3, 15)
FILE = (
    "Participant wise Open Interest (no. of contracts) in Futures & Options as on Mar 15, 2024\n"
    "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,"
    "Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,"
    "Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
    "Total Long Contracts,Total Short Contracts\n"
    "Client,100,200,10,20,1,2,3,4,5,6,7,8,124,242\n"
    "DII,300,100,0,0,0,0,0,0,0,0,0,0,300,100\n"
    "FII,150000,250000,900000,800000,400000,500000,450000,350000,1,2,3,4,1,1\n"
    "Pro,5,5,5,5,5,5,5,5,5,5,5,5,60,60\n"
    "TOTAL,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n"
)


def test_the_four_participants_are_read_and_the_total_row_is_left_out() -> None:
    rows = {r.participant: r for r in parse_participant_file(FILE, DAY, "oi")}

    assert set(rows) == {"Client", "DII", "FII", "Pro"}
    fii = rows["FII"]
    assert (fii.fut_index_long, fii.fut_index_short) == (150000, 250000)
    assert fii.net_index_futures == -100000
    assert fii.net_index_calls == -50000 and fii.net_index_puts == 150000
    assert fii.index_futures_long_share == pytest.approx(0.375)
    assert fii.day == DAY and fii.kind == "oi"


def test_a_holiday_file_is_empty_and_a_short_row_is_an_error() -> None:
    assert parse_participant_file("", DAY, "oi") == []
    with pytest.raises(ValueError, match="numbers"):
        parse_participant_file("Client Type,a,b\nFII,1,2\n", DAY, "oi")


def test_a_days_file_is_usable_from_the_next_open_not_that_evening() -> None:
    assert available_at(DAY) == datetime(2024, 3, 15, 23, 59, tzinfo=IST)
    assert available_at(DAY) > datetime(2024, 3, 15, 15, 30, tzinfo=IST)  # after the close
    assert available_at(DAY) < datetime(2024, 3, 18, 9, 15, tzinfo=IST)  # before the next open


def test_pages_are_an_oi_and_a_volume_file_per_day_and_a_404_is_absent_not_a_failure() -> None:
    pages = participant_pages([DAY])

    assert [p.name for p in pages] == [
        "participant/oi/2024-03-15.csv", "participant/vol/2024-03-15.csv",
    ]  # fmt: skip
    assert pages[0].url.endswith("/nsccl/fao_participant_oi_15032024.csv")
    assert all(p.absent_is_data for p in pages)


class NoSleep(Sleeper):
    async def sleep(self, seconds: float) -> None:
        return None


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 26, tzinfo=UTC)


def test_holidays_do_not_halt_the_run_and_are_not_asked_for_twice(tmp_path: Path) -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(404)

    days = [date(2024, 1, 25), date(2024, 1, 26), date(2024, 1, 29), date(2024, 1, 30)]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run = PageCollector(
        PoliteGet(client, NoSleep(), 1.0), tmp_path / "raw", FetchLedger(tmp_path / "l.jsonl"),
        Clock(),
    )  # fmt: skip

    async def go() -> list[str]:
        async with client:
            failed = await run.run(participant_pages(days), lambda _: None)
            await run.run(participant_pages(days), lambda _: None)
            return failed

    assert asyncio.run(go()) == []  # eight 404s in a row, none a failure
    assert len(asked) == 8  # and the second run asked for nothing
    assert (tmp_path / "raw" / "participant" / "oi" / "2024-01-26.csv").read_bytes() == b""
    assert FetchLedger(tmp_path / "l.jsonl").records()[0].count == 0
