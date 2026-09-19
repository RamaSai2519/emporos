"""Composition root for the historical-data stack (Phase 6): backfill, gap reconciliation, calendar.

Everything is injected, so the wiring is testable with doubles; `history_runtime` builds the real
dependencies for the CLI. All writes go through `CandleRepository` (the only allowed candle path).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.core.errors import ConfigurationError
from emporos.domain.instruments import (
    Exchange,
    Instrument,
    InstrumentResolver,
    UnknownInstrumentError,
)
from emporos.history.backfill import DEFAULT_CHUNK_DAYS, BackfillOrchestrator
from emporos.history.calendar import CalendarSeeder, CalendarStore, StoredTradingCalendar
from emporos.history.gaps import GapDetector, GapFiller, HistoryReconciler
from emporos.history.grid import SessionGrid
from emporos.history.source import CoverageStore, HistoricalCandleSource
from emporos.persistence.candles import CandleRepository


@dataclass(frozen=True)
class HistoryStack:
    backfill: BackfillOrchestrator
    detector: GapDetector
    filler: GapFiller
    reconciler: HistoryReconciler
    seeder: CalendarSeeder
    grid: SessionGrid


@dataclass(frozen=True, kw_only=True)
class HistoryComposer:
    source: HistoricalCandleSource
    repository: CandleRepository
    coverage: CoverageStore
    calendar_store: CalendarStore
    calendar: StoredTradingCalendar
    clock: Clock
    alerts: AlertSink | None = None
    chunk_days: int = DEFAULT_CHUNK_DAYS

    def build(self) -> HistoryStack:
        grid = SessionGrid(self.calendar)
        detector = GapDetector(self.repository, self.coverage, grid)
        filler = GapFiller(
            self.source, self.repository, self.repository, self.coverage, grid, self.clock
        )
        return HistoryStack(
            backfill=BackfillOrchestrator(
                self.source,
                self.repository,
                self.coverage,
                grid,
                self.clock,
                self.alerts,
                self.chunk_days,
            ),
            detector=detector,
            filler=filler,
            reconciler=HistoryReconciler(detector, filler),
            seeder=CalendarSeeder(self.source, self.calendar_store, grid.window),
            grid=grid,
        )


def resolve_symbols(
    resolver: InstrumentResolver, exchange: Exchange, symbols: Sequence[str]
) -> list[Instrument]:
    """Trading symbols (e.g. `SBIN-EQ`) -> instruments, failing clearly on any unknown one."""
    if not symbols:
        raise ConfigurationError("give at least one symbol")
    instruments: list[Instrument] = []
    unknown: list[str] = []
    for symbol in symbols:
        try:
            instruments.append(resolver.by_symbol(exchange, symbol))
        except UnknownInstrumentError:
            unknown.append(symbol)
    if unknown:
        raise ConfigurationError(f"unknown symbol(s) on {exchange.value}: {', '.join(unknown)}")
    return instruments
