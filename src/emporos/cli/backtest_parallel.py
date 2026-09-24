"""Composition of the parallel curation: what a worker process is built from, and the pool.

A worker holds no database connection and no credentials: it is rebuilt from a `CurationRecipe`
(paths, the instrument eras, the risk limits) and reads candles from files only. The parent
prefetches every month a batch reads (through the read-through cache, two reads at a time) into the
cache, or, for a month too recent to cache, into a snapshot directory that lives as long as the run.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from emporos.backtest.batch import Backtester, BatchBacktester
from emporos.backtest.costs import EarliestBeforeFirst, ScheduleSource, StrictSchedules
from emporos.backtest.engine import BacktestEngine
from emporos.backtest.job import ResolverTickSizes
from emporos.backtest.parallel import PrefetchingBatch, ProcessPoolBatch, SpecNeeds
from emporos.backtest.risk_gate import RiskGateFactory
from emporos.backtest.universe import AsOfInstruments, InstrumentEra
from emporos.backtest.vault import VaultedCandleReader, VaultGate
from emporos.cli.strategy_composition import build_registry
from emporos.persistence.candle_cache import (
    CachingCandleReader,
    CandleCacheFiles,
    CandlePrefetcher,
    FileCandleReader,
)
from emporos.persistence.candles import CandleReader
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.risk.limits import RiskLimits


@dataclass(frozen=True)
class CurationRecipe:
    """Everything a worker needs to build the same engine the serial curation builds."""

    cache_root: Path
    snapshot_root: Path
    eras: tuple[InstrumentEra, ...]
    universe_at: datetime
    assume_current_universe: bool
    assume_fees: bool
    limits: RiskLimits
    vault: VaultGate  # a worker is a process of its own: it must carry the seal with it

    def candle_reader(self) -> CandleReader:
        """What the worker reads bars through: cache files only, and never the vault."""
        return VaultedCandleReader(
            FileCandleReader(
                [CandleCacheFiles(self.cache_root), CandleCacheFiles(self.snapshot_root)]
            ),
            self.vault,
        )

    def build(self) -> Backtester:
        universe = AsOfInstruments(self.eras).as_of(
            self.universe_at, assume_earliest_before_history=self.assume_current_universe
        )
        library = FeeScheduleLibrary.from_directory()

        def schedules() -> ScheduleSource:
            if self.assume_fees:
                return EarliestBeforeFirst(library)
            return StrictSchedules(library)

        return BacktestEngine(
            self.candle_reader(), build_registry(), ResolverTickSizes(universe.resolver), schedules,
            gate=RiskGateFactory(self.limits),
        )  # fmt: skip


@contextmanager
def curation_batch(
    workers: int,
    reader: CachingCandleReader,
    cache_root: Path,
    make_recipe: Callable[[Path], CurationRecipe],
) -> Iterator[BatchBacktester | None]:
    """The batch runner for `workers` processes, or None for one worker: the curation then runs
    every backtest in-process, one after another (the reference behaviour)."""
    if workers == 1:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="emporos-snapshot-") as snapshot_dir:
        snapshot = Path(snapshot_dir)
        prefetcher = CandlePrefetcher(
            reader, CandleCacheFiles(cache_root), CandleCacheFiles(snapshot)
        )
        with ProcessPoolBatch(make_recipe(snapshot), workers) as pool:
            yield PrefetchingBatch(pool, prefetcher, SpecNeeds())
