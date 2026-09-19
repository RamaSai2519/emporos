"""Resumable historical backfill (EM-55, plan.md Phase 6).

For each instrument it works out which trading days still lack a *complete* coverage record,
groups consecutive missing days into chunks no longer than the source's per-request limit, and for
each chunk: fetch -> upsert candles (through `CandleWriter`, i.e. `CandleRepository.upsert`) ->
record per-day coverage. The coverage record is written only AFTER the candles are, so:

* killing the process at any point loses nothing: the chunk in flight is simply redone on
  restart (the upsert is idempotent, so redoing it creates no duplicates), and every finished
  day is skipped;
* a day is only ever marked covered once its bars are durably written.

`getCandleData` is slow and defective (spurious "access rate" denials), so the transport's bounded
backoff does the retrying; a chunk that still fails is reported and left for the next run — it never
aborts the rest of the run and is never recorded as covered.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from emporos.broker.errors import BrokerError
from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.coverage import DayCoverage
from emporos.domain.instruments import Instrument
from emporos.history.grid import SessionGrid, compress
from emporos.history.source import CoverageStore, HistoricalCandleSource
from emporos.marketdata.timeframes import derive
from emporos.persistence.candles import CandleWriter

_LOG = logging.getLogger(__name__)
# Angel One serves at most 30 days of 1m per request and silently truncates beyond that.
DEFAULT_CHUNK_DAYS = 28


@dataclass(frozen=True)
class Chunk:
    instrument: Instrument
    first: date
    last: date


@dataclass
class BackfillReport:
    chunks_fetched: int = 0
    days_already_covered: int = 0
    candles_written: int = 0
    failed_chunks: list[tuple[str, date, date]] = field(default_factory=list)
    empty_days: list[tuple[str, date]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed_chunks


class BackfillOrchestrator:
    def __init__(
        self,
        source: HistoricalCandleSource,
        writer: CandleWriter,
        coverage: CoverageStore,
        grid: SessionGrid,
        clock: Clock,
        alerts: AlertSink | None = None,
        chunk_days: int = DEFAULT_CHUNK_DAYS,
        derive_higher: bool = True,
    ) -> None:
        if not 1 <= chunk_days <= 30:
            raise ConfigurationError("chunk_days must be 1..30 (the broker truncates beyond 30)")
        self._source = source
        self._writer = writer
        self._coverage = coverage
        self._grid = grid
        self._clock = clock
        self._alerts = alerts
        self._chunk_days = chunk_days
        self._derive_higher = derive_higher

    async def run(
        self, instruments: Sequence[Instrument], first: date, last: date
    ) -> BackfillReport:
        """Backfill 1m candles for trading days `first..last` (IST dates), resuming where a
        previous run stopped."""
        report = BackfillReport()
        for instrument in instruments:
            for chunk in await self._plan(instrument, first, last, report):
                await self._run_chunk(chunk, report)
        if report.failed_chunks:
            self._alert(
                "history.backfill_incomplete", f"{len(report.failed_chunks)} chunk(s) failed"
            )
        return report

    async def plan(self, instrument: Instrument, first: date, last: date) -> list[Chunk]:
        """The chunks a run would fetch now (covered days excluded)."""
        return await self._plan(instrument, first, last, BackfillReport())

    async def _plan(
        self, instrument: Instrument, first: date, last: date, report: BackfillReport
    ) -> list[Chunk]:
        days = self._grid.trading_days(first, last)
        covered = await self._coverage.get_days(instrument.instrument_id, Timeframe.M1, first, last)
        missing = [d for d in days if not (d in covered and covered[d].complete)]
        report.days_already_covered += len(days) - len(missing)
        return self._chunks(instrument, missing)

    def _chunks(self, instrument: Instrument, days: list[date]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for day in days:
            current = chunks[-1] if chunks else None
            if current is not None and (day - current.first).days < self._chunk_days:
                chunks[-1] = Chunk(instrument, current.first, day)
            else:
                chunks.append(Chunk(instrument, day, day))
        return chunks

    async def _run_chunk(self, chunk: Chunk, report: BackfillReport) -> None:
        window = self._grid.window
        start, end = window.open_at(chunk.first), window.close_at(chunk.last)
        try:
            fetched = await self._source.fetch(chunk.instrument, Timeframe.M1, start, end)
        except BrokerError:
            _LOG.exception("backfill chunk failed for %s", chunk.instrument.instrument_id)
            report.failed_chunks.append((chunk.instrument.instrument_id, chunk.first, chunk.last))
            return
        bars = [b for b in fetched if window.contains(b.ts) and start <= b.ts < end]
        await self._writer.upsert(self._with_higher(bars))
        await self._coverage.save(self._coverages(chunk, bars, report))
        report.chunks_fetched += 1
        report.candles_written += len(bars)

    def _with_higher(self, bars: list[Candle]) -> list[Candle]:
        return [*bars, *derive(bars, window=self._grid.window)] if self._derive_higher else bars

    def _coverages(
        self, chunk: Chunk, bars: list[Candle], report: BackfillReport
    ) -> list[DayCoverage]:
        have = {b.ts for b in bars}
        now = self._clock.now()
        coverages = []
        for day in self._grid.trading_days(chunk.first, chunk.last):
            expected = self._grid.minutes(day)
            present = [m for m in expected if m in have]
            empty = not present
            if empty:
                report.empty_days.append((chunk.instrument.instrument_id, day))
                self._alert(
                    "history.empty_trading_day",
                    f"{chunk.instrument.instrument_id} has no bars on {day} (holiday missing "
                    "from the calendar, or a truncated response)",
                )
            coverages.append(
                DayCoverage(
                    instrument_id=chunk.instrument.instrument_id,
                    timeframe=Timeframe.M1,
                    day=day,
                    absent=compress(m for m in expected if m not in have),
                    fetched_at=now,
                    complete=True,
                    empty=empty,
                )
            )
        return coverages

    def _alert(self, name: str, message: str) -> None:
        if self._alerts is not None:
            self._alerts.raise_alert(name, message)
