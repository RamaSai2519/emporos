"""Fetches the declared reference series as candles (EM-191 D2, EDGE_SEARCH_PLAN.md §4.2).

One job: verify the catalog against the broker's master, then hand the series to the bar fetcher
the equities already use (throttled, chunked to the broker's 28-day limit, resumable through its
coverage ledger). Nothing here reads a result, so it is not behind the vault: it copies bars, it
does not analyse them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.domain.reference_series import ReferenceSeries
from emporos.history.derived import DerivedBackfillReport
from emporos.instruments.reference_series import ReferenceSeriesCatalog, ReferenceSeriesVerifier

__all__ = ["ReferenceBarFetch", "ReferenceCacheWarm", "SeriesBarFetcher", "SeriesCandleSource"]


class SeriesBarFetcher(Protocol):
    async def run(
        self, instruments: Sequence[Instrument], first: date, last: date
    ) -> DerivedBackfillReport: ...


class ReferenceBarFetch:
    def __init__(
        self,
        catalog: ReferenceSeriesCatalog,
        fetcher: SeriesBarFetcher,
        verifier: ReferenceSeriesVerifier | None = None,
    ) -> None:
        self._catalog = catalog
        self._fetcher = fetcher
        self._verifier = verifier or ReferenceSeriesVerifier()

    async def run(
        self,
        master_rows: Sequence[Mapping[str, Any]],
        first: date,
        last: date,
        symbols: Sequence[str] = (),
    ) -> tuple[Sequence[ReferenceSeries], DerivedBackfillReport]:
        chosen = self._catalog.select(symbols)
        self._verifier.verify(
            chosen, master_rows
        )  # before any request: a moved token fetches wrongly
        report = await self._fetcher.run([s.fetch_handle() for s in chosen], first, last)
        return chosen, report


class SeriesCandleSource(Protocol):
    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...


class ReferenceCacheWarm:
    """Reads each declared series once, so its closed months land in the local candle cache and
    research never re-reads them from the shared cluster. The source is the cache reader itself,
    outside the vault: warming copies bars and produces no result."""

    def __init__(self, catalog: ReferenceSeriesCatalog, source: SeriesCandleSource) -> None:
        self._catalog = catalog
        self._source = source

    async def warm(
        self, first: date, last: date, timeframe: Timeframe, symbols: Sequence[str] = ()
    ) -> dict[str, int]:
        """Bars read per series id over IST days `first..last`, one series at a time (a gentle
        read pattern for the shared Atlas)."""
        start = datetime.combine(first, time(0, 0), tzinfo=IST)
        end = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=IST)
        found: dict[str, int] = {}
        for series in self._catalog.select(symbols):
            bars = await self._source.get_range(series.series_id, timeframe, start, end)
            found[series.series_id] = len(bars)
        return found
