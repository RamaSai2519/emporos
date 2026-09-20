"""Backfill ONE derived timeframe without storing the 1m bars underneath it (EM-108).

`BackfillOrchestrator` stores 1m bars, which older than the hot retention (90 days) belong in the
cold S3 archive. With no bucket configured that tier does not exist, yet a year of 5m history is
well inside the hot retention for 5m (365 days). So this fetches 1m in chunks the broker will
serve, derives the wanted timeframe with the SAME `derive` the live pipeline uses (so a backtest's
5m bar is built exactly like a live one), and writes only that timeframe.

It keeps no coverage ledger: that ledger is per 1m minute. What it reports instead is how many bars
each trading day produced, so a truncated or empty day is visible to whoever reads the report.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from emporos.broker.errors import BrokerError
from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.history.source import HistoricalCandleSource
from emporos.marketdata.session import SessionWindow
from emporos.marketdata.timeframes import DERIVED_TIMEFRAMES, derive
from emporos.persistence.candles import CandleWriter

_LOG = logging.getLogger(__name__)
DEFAULT_CHUNK_DAYS = 28  # the broker truncates a 1m request beyond 30 days, silently


@dataclass
class DerivedBackfillReport:
    chunks_fetched: int = 0
    bars_written: int = 0
    failed_chunks: list[tuple[str, date, date]] = field(default_factory=list)
    bars_per_day: dict[tuple[str, date], int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed_chunks


class DerivedBarBackfill:
    def __init__(
        self,
        source: HistoricalCandleSource,
        writer: CandleWriter,
        window: SessionWindow,
        keep: Timeframe,
        chunk_days: int = DEFAULT_CHUNK_DAYS,
    ) -> None:
        if keep not in DERIVED_TIMEFRAMES:
            raise ConfigurationError(f"{keep.value} is not a timeframe derived from 1m bars")
        if not 1 <= chunk_days <= 30:
            raise ConfigurationError("chunk_days must be 1..30 (the broker truncates beyond 30)")
        self._source = source
        self._writer = writer
        self._window = window
        self._keep = keep
        self._chunk_days = chunk_days

    async def run(
        self, instruments: Sequence[Instrument], first: date, last: date
    ) -> DerivedBackfillReport:
        """Fetch IST days `first..last` inclusive, one instrument at a time, chunk by chunk."""
        if first > last:
            raise ConfigurationError("the first day must not be after the last")
        report = DerivedBackfillReport()
        for instrument in instruments:
            for chunk_first, chunk_last in self._chunks(first, last):
                await self._chunk(instrument, chunk_first, chunk_last, report)
        return report

    def _chunks(self, first: date, last: date) -> list[tuple[date, date]]:
        chunks: list[tuple[date, date]] = []
        cursor = first
        while cursor <= last:
            end = min(cursor + timedelta(days=self._chunk_days - 1), last)
            chunks.append((cursor, end))
            cursor = end + timedelta(days=1)
        return chunks

    async def _chunk(
        self, instrument: Instrument, first: date, last: date, report: DerivedBackfillReport
    ) -> None:
        start, end = self._window.open_at(first), self._window.close_at(last)
        try:
            fetched = await self._source.fetch(instrument, Timeframe.M1, start, end)
        except BrokerError:
            _LOG.exception("bar chunk failed for %s", instrument.instrument_id)
            report.failed_chunks.append((instrument.instrument_id, first, last))
            return
        minutes = [b for b in fetched if self._window.contains(b.ts) and start <= b.ts < end]
        kept = [
            b for b in derive(minutes, (self._keep,), self._window) if b.timeframe is self._keep
        ]
        await self._writer.upsert(kept)
        report.chunks_fetched += 1
        report.bars_written += len(kept)
        self._count_days(instrument, kept, report)

    def _count_days(
        self, instrument: Instrument, bars: list[Candle], report: DerivedBackfillReport
    ) -> None:
        for bar in bars:
            key = (instrument.instrument_id, bar.ts.astimezone(IST).date())
            report.bars_per_day[key] = report.bars_per_day.get(key, 0) + 1
