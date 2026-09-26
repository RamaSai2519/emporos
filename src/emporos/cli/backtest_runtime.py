"""Composition root for `emporos backtest`: read-only Mongo (candles, instrument history), the
strategy registry, the fee schedules. No broker, no credentials, nothing that can place an order.

The candle reader is a `CandleRepository`, the only allowed path to candles. With no cold tier
configured (no S3 bucket, no local directory) it is `DisabledColdArchive`: ranges older than the
hot retention read as empty, which the backtest then reports as missing bars rather than inventing
them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pymongo.asynchronous.database import AsyncDatabase

from emporos.backtest.costs import EarliestBeforeFirst, ScheduleSource, StrictSchedules
from emporos.backtest.engine import BacktestResult
from emporos.backtest.job import BacktestJob, BacktestRequest
from emporos.backtest.progress import BacktestProgressSink
from emporos.backtest.universe import AsOfInstruments, InstrumentEra
from emporos.backtest.vault import VaultedCandleReader
from emporos.cli.cold_storage import cold_archive
from emporos.cli.strategy_composition import build_registry
from emporos.cli.vault_files import VaultFiles
from emporos.core.clock import SystemClock
from emporos.core.config import Settings
from emporos.core.paths import research_dir
from emporos.domain.instruments import Exchange, Instrument, InstrumentResolver
from emporos.history.calendar import StoredTradingCalendar
from emporos.history.quarantine import CorporateActionQuarantine
from emporos.persistence.calendar_store import MongoCalendarStore
from emporos.persistence.candle_cache import CachingCandleReader, CandleCacheFiles
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleReader, CandleRepository
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.quarantine_store import MongoQuarantineStore
from emporos.persistence.records import InstrumentRecord, InstrumentVersionRecord
from emporos.persistence.repositories import InstrumentRepository, InstrumentVersionRepository
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver


@dataclass(frozen=True)
class BacktestRuntime:
    reader: CandleReader
    instruments: AsOfInstruments
    database: AsyncDatabase  # type: ignore[type-arg]
    cache: CachingCandleReader | None = None
    eras: tuple[InstrumentEra, ...] = ()
    calendar: StoredTradingCalendar = field(default_factory=StoredTradingCalendar)
    quarantine: CorporateActionQuarantine = field(default_factory=CorporateActionQuarantine)


class InstrumentErasReader:
    """The instrument master's history as eras: current definitions (open-ended, from their own
    `valid_from`) plus every superseded one (`instrument_versions`, closed)."""

    def __init__(self, database: AsyncDatabase) -> None:  # type: ignore[type-arg]
        self._current = InstrumentRepository(database)
        self._versions = InstrumentVersionRepository(database)

    async def read(self) -> list[InstrumentEra]:
        current = [self._current_era(r) for r in await self._current.all()]
        closed = [self._closed_era(r) for r in await self._versions.find({})]
        return [*current, *closed]

    @staticmethod
    def _current_era(record: InstrumentRecord) -> InstrumentEra:
        instrument = Instrument(
            Exchange(record.exchange), record.token, record.tradingsymbol, record.name,
            record.lot_size, record.tick_size,
        )  # fmt: skip
        return InstrumentEra(instrument, record.valid_from, None)

    @staticmethod
    def _closed_era(record: InstrumentVersionRecord) -> InstrumentEra:
        instrument = Instrument(
            Exchange(record.exchange), record.token, record.tradingsymbol, record.name,
            record.lot_size, record.tick_size,
        )  # fmt: skip
        return InstrumentEra(instrument, record.valid_from, record.valid_to)


def candle_cache_root(settings: Settings) -> Path:
    if settings.candle_cache_dir:
        return Path(settings.candle_cache_dir).expanduser()
    return research_dir() / "candles"


@asynccontextmanager
async def open_backtest_runtime(settings: Settings) -> AsyncIterator[BacktestRuntime]:
    mongo = MongoClientFactory(settings)
    try:
        database = mongo.database()
        repository = CandleRepository(MongoCandleStore(database), cold_archive(settings))
        cache = CachingCandleReader(
            repository, CandleCacheFiles(candle_cache_root(settings)), SystemClock()
        )
        # Analysis reads through the vault: it cannot see the sealed days (EM-191 §4.3). The
        # cache itself stays open for warming and prefetching, which produce no result.
        reader = VaultedCandleReader(cache, VaultFiles().load())
        eras = await InstrumentErasReader(database).read()
        calendar = await StoredTradingCalendar.from_store(MongoCalendarStore(database))
        quarantine = CorporateActionQuarantine(await MongoQuarantineStore(database).load_all())
        yield BacktestRuntime(
            reader, AsOfInstruments(eras), database, cache, tuple(eras), calendar, quarantine
        )
    finally:
        await mongo.close()


async def run_backtest(
    settings: Settings,
    strategy_file: Path,
    request: BacktestRequest,
    assume_earliest_fees: bool,
    progress: BacktestProgressSink | None = None,
) -> BacktestResult:
    registry = build_registry()
    library = FeeScheduleLibrary.from_directory()

    def schedules() -> ScheduleSource:
        if assume_earliest_fees:
            return EarliestBeforeFirst(library)
        return StrictSchedules(library)

    def config_for(resolver: InstrumentResolver) -> ResolvedStrategyConfig:
        return StrategyConfigLoader(StrategyConfigResolver(registry, resolver)).load_file(
            strategy_file
        )

    async with open_backtest_runtime(settings) as runtime:
        job = BacktestJob(
            runtime.reader, registry, runtime.instruments, config_for, schedules,
            progress=progress, calendar=runtime.calendar, quarantine=runtime.quarantine,
        )  # fmt: skip
        return await job.run(request)
