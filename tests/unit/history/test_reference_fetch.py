"""EM-191 D2: reference series are verified, then handed to the bar fetcher as fetch handles."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money
from emporos.history.derived import DerivedBackfillReport
from emporos.history.reference_fetch import ReferenceBarFetch, ReferenceCacheWarm
from emporos.instruments.reference_series import ReferenceSeriesCatalog, ReferenceSeriesMismatch

ROWS = json.loads(Path("tests/fixtures/reference_series_master_rows.json").read_text())
FIRST, LAST = date(2026, 8, 1), date(2026, 8, 31)


class Fetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], date, date]] = []

    async def run(
        self, instruments: Sequence[Instrument], first: date, last: date
    ) -> DerivedBackfillReport:
        self.calls.append(([i.instrument_id for i in instruments], first, last))
        return DerivedBackfillReport(chunks_fetched=len(instruments))


async def test_every_declared_series_is_fetched_by_default() -> None:
    fetcher = Fetcher()

    chosen, report = await ReferenceBarFetch(ReferenceSeriesCatalog.load(), fetcher).run(
        ROWS, FIRST, LAST
    )

    assert len(chosen) == 15 and report.ok
    ids, first, last = fetcher.calls[0]
    assert len(ids) == 15 and ids[0] == "NSE:99926000" and (first, last) == (FIRST, LAST)


async def test_named_series_are_fetched_alone() -> None:
    fetcher = Fetcher()

    chosen, _ = await ReferenceBarFetch(ReferenceSeriesCatalog.load(), fetcher).run(
        ROWS, FIRST, LAST, ["India VIX"]
    )

    assert [s.symbol for s in chosen] == ["India VIX"]
    assert fetcher.calls[0][0] == ["NSE:99926017"]


async def test_a_moved_token_stops_the_fetch_before_any_request() -> None:
    fetcher = Fetcher()
    rows = [r for r in ROWS if r["token"] != "99926017"]

    with pytest.raises(ReferenceSeriesMismatch):
        await ReferenceBarFetch(ReferenceSeriesCatalog.load(), fetcher).run(rows, FIRST, LAST)

    assert fetcher.calls == []


class Source:
    def __init__(self) -> None:
        self.asked: list[tuple[str, Timeframe, datetime, datetime]] = []

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.asked.append((instrument_id, timeframe, start, end))
        price = Money.of(100)
        return [
            Candle(instrument_id, timeframe, start.astimezone(UTC), price, price, price, price, 0)
        ]


async def test_warming_reads_each_series_once_over_whole_ist_days() -> None:
    source = Source()

    found = await ReferenceCacheWarm(ReferenceSeriesCatalog.load(), source).warm(
        FIRST, LAST, Timeframe.M5, ["Nifty 50", "India VIX"]
    )

    assert found == {"NSE:99926000": 1, "NSE:99926017": 1}
    assert [a[0] for a in source.asked] == ["NSE:99926000", "NSE:99926017"]
    _, timeframe, start, end = source.asked[0]
    assert timeframe is Timeframe.M5
    assert (start.astimezone(IST).date(), start.astimezone(IST).hour) == (FIRST, 0)
    assert end.astimezone(IST).date() == date(2026, 9, 1)  # the whole of the last day
