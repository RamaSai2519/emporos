"""Gap detection against the trading calendar, and gap-only re-fetching (EM-56).

A *gap* is a session minute of a trading day that (a) has no stored 1m bar and (b) the broker has
not already confirmed absent. Detection reads what is actually STORED, so a bar lost after a
successful backfill is found; and because confirmed-absent minutes are subtracted, the broker's
own silent minutes (it omits ~14 a day even for SBIN) are never chased forever. Holidays and
weekends are not trading days, so they can never be gaps.

Filling fetches only the gaps (one request per affected day), writes only the bars that fall inside
a gap (never overwriting existing bars), rebuilds the higher timeframes for the touched days, and
records what the broker still did not return as newly confirmed-absent.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from emporos.broker.errors import BrokerError
from emporos.core.clock import Clock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.coverage import DayCoverage, TimeRange
from emporos.domain.instruments import Instrument
from emporos.history.grid import SessionGrid, compress
from emporos.history.source import CoverageStore, HistoricalCandleSource
from emporos.marketdata.timeframes import derive
from emporos.persistence.candles import CandleReader, CandleWriter

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Gap:
    instrument_id: str
    day: date
    range: TimeRange


class GapDetector:
    def __init__(self, reader: CandleReader, coverage: CoverageStore, grid: SessionGrid) -> None:
        self._reader = reader
        self._coverage = coverage
        self._grid = grid

    async def find(self, instrument_id: str, first: date, last: date) -> list[Gap]:
        days = self._grid.trading_days(first, last)
        if not days:
            return []
        window = self._grid.window
        stored = await self._reader.get_range(
            instrument_id, Timeframe.M1, window.open_at(days[0]), window.close_at(days[-1])
        )
        have = {c.ts for c in stored}
        covered = await self._coverage.get_days(instrument_id, Timeframe.M1, first, last)
        gaps: list[Gap] = []
        for day in days:
            confirmed = covered.get(day)
            missing = [
                m
                for m in self._grid.minutes(day)
                if m not in have and not (confirmed and confirmed.is_absent(m))
            ]
            gaps.extend(Gap(instrument_id, day, r) for r in compress(missing))
        return gaps


@dataclass
class FillReport:
    gaps_found: int = 0
    gaps_filled: int = 0
    candles_written: int = 0
    failed_days: list[tuple[str, date]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed_days


class GapFiller:
    def __init__(
        self,
        source: HistoricalCandleSource,
        writer: CandleWriter,
        reader: CandleReader,
        coverage: CoverageStore,
        grid: SessionGrid,
        clock: Clock,
    ) -> None:
        self._source = source
        self._writer = writer
        self._reader = reader
        self._coverage = coverage
        self._grid = grid
        self._clock = clock

    async def fill(self, instrument: Instrument, gaps: list[Gap]) -> FillReport:
        report = FillReport(gaps_found=len(gaps))
        by_day: dict[date, list[Gap]] = defaultdict(list)
        for gap in gaps:
            by_day[gap.day].append(gap)
        for day, day_gaps in sorted(by_day.items()):
            try:
                await self._fill_day(instrument, day, day_gaps, report)
            except BrokerError:
                _LOG.exception("gap fill failed for %s on %s", instrument.instrument_id, day)
                report.failed_days.append((instrument.instrument_id, day))
        return report

    async def _fill_day(
        self, instrument: Instrument, day: date, gaps: list[Gap], report: FillReport
    ) -> None:
        start = min(g.range.start for g in gaps)
        end = max(g.range.end for g in gaps)
        fetched = await self._source.fetch(instrument, Timeframe.M1, start, end)
        wanted = [g.range for g in gaps]
        returned = [b for b in fetched if any(r.contains(b.ts) for r in wanted)]  # gaps only
        if returned:
            await self._writer.upsert(returned)
            await self._writer.upsert(await self._rebuild_higher(instrument, day))
        await self._record_absent(instrument, day, gaps, {b.ts for b in returned})
        report.gaps_filled += len(gaps)
        report.candles_written += len(returned)

    async def _rebuild_higher(self, instrument: Instrument, day: date) -> list[Candle]:
        window = self._grid.window
        minutes = await self._reader.get_range(
            instrument.instrument_id, Timeframe.M1, window.open_at(day), window.close_at(day)
        )
        return derive(minutes, window=window)

    async def _record_absent(
        self, instrument: Instrument, day: date, gaps: list[Gap], returned: set[datetime]
    ) -> None:
        still_missing = [
            m
            for g in gaps
            for m in self._grid.minutes(day)
            if g.range.contains(m) and m not in returned
        ]
        found = await self._coverage.get_days(instrument.instrument_id, Timeframe.M1, day, day)
        existing = found.get(day)
        known_absent = existing.absent if existing else ()
        merged = compress(
            m for r in (*known_absent, *compress(still_missing)) for m in self._minutes_in(r)
        )
        await self._coverage.save(
            [
                DayCoverage(
                    instrument_id=instrument.instrument_id,
                    timeframe=Timeframe.M1,
                    day=day,
                    absent=merged,
                    fetched_at=self._clock.now(),
                    complete=existing.complete if existing else False,
                    empty=existing.empty if existing else False,
                )
            ]
        )

    def _minutes_in(self, span: TimeRange) -> list[datetime]:
        return [m for m in self._grid.minutes(self._grid.day_of(span.start)) if span.contains(m)]


class HistoryReconciler:
    """Detect gaps for each instrument and fill them: the "keep history complete" job."""

    def __init__(self, detector: GapDetector, filler: GapFiller) -> None:
        self._detector = detector
        self._filler = filler

    async def reconcile(
        self, instruments: list[Instrument], first: date, last: date
    ) -> dict[str, FillReport]:
        reports: dict[str, FillReport] = {}
        for instrument in instruments:
            gaps = await self._detector.find(instrument.instrument_id, first, last)
            reports[instrument.instrument_id] = await self._filler.fill(instrument, gaps)
        return reports
