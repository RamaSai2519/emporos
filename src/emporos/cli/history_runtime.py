"""Builds the REAL dependencies for the history commands from `Settings` and tears them down.

Only the CLI composition root may do this: it opens Mongo and S3 and logs in to Angel One (which
supersedes any other session for the client code — see `broker.angelone.session`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pymongo.asynchronous.database import AsyncDatabase

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill, BarRejections
from emporos.broker.angelone.factory import AngelOneStackFactory
from emporos.broker.backoff import RandomJitter
from emporos.cli.cold_storage import cold_archive, cold_object_store
from emporos.cli.history_composition import HistoryComposer, HistoryStack
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.domain.candles import Timeframe
from emporos.history.calendar import StoredTradingCalendar
from emporos.history.derived import DerivedBarBackfill
from emporos.history.source import HistoricalCandleSource
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.store import MongoInstrumentMasterStore
from emporos.marketdata.session import SessionWindow
from emporos.persistence.calendar_store import MongoCalendarStore
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candle_rollup import CandleRollup
from emporos.persistence.candles import CandleRepository
from emporos.persistence.collections import Collection
from emporos.persistence.coverage import MongoCoverageStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.object_coverage import ObjectCoverageStore
from emporos.persistence.placement import RetentionPlacement
from emporos.persistence.records import InstrumentRecord
from emporos.persistence.repositories import InstrumentRepository, InstrumentVersionRepository
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.persistence.staged_load import StagedCollectionLoader
from emporos.persistence.transactions import TransactionRunner


@dataclass(frozen=True)
class HistoryRuntime:
    stack: HistoryStack
    instruments: InstrumentCache


@dataclass(frozen=True)
class BarFetchRuntime:
    fetcher: DerivedBarBackfill
    instruments: InstrumentCache
    rejected: (
        BarRejections  # the bars the broker sent that were not valid candles, and were skipped
    )


def instrument_master(
    mongo: MongoClientFactory,
    database: AsyncDatabase,  # type: ignore[type-arg]
) -> MongoInstrumentMasterStore:
    return MongoInstrumentMasterStore(
        TransactionRunner(mongo.client),
        InstrumentRepository(database),
        InstrumentVersionRepository(database),
        StagedCollectionLoader(
            database, PLATFORM_SCHEMA.spec_for(Collection.INSTRUMENTS), InstrumentRecord
        ),
    )


async def _instrument_cache(
    mongo: MongoClientFactory,
    database: AsyncDatabase,  # type: ignore[type-arg]
) -> InstrumentCache:
    master = instrument_master(mongo, database)
    cache = InstrumentCache()
    await cache.load_from(master)
    return cache


@dataclass(frozen=True)
class BrokerHistoryRuntime:
    source: HistoricalCandleSource
    instruments: InstrumentCache


@asynccontextmanager
async def open_broker_history_runtime(settings: Settings) -> AsyncIterator[BrokerHistoryRuntime]:
    """Read-only: the instrument master (Mongo) and the broker's history endpoint. Nothing is
    written anywhere."""
    mongo = MongoClientFactory(settings)
    broker = AngelOneStackFactory(settings, SystemClock(), AsyncioSleeper(), RandomJitter()).build()
    try:
        instruments = await _instrument_cache(mongo, mongo.database())
        yield BrokerHistoryRuntime(
            AngelOneCandleBackfill(AngelOneApi(broker.transport)), instruments
        )
    finally:
        try:
            await broker.sessions.logout()
        finally:
            await broker.aclose()
            await mongo.close()


@asynccontextmanager
async def open_bar_fetch_runtime(
    settings: Settings, keep: Timeframe
) -> AsyncIterator[BarFetchRuntime]:
    """Mongo, the broker and the cold archive: bars older than the hot retention are written to
    Parquet, not to Mongo."""
    clock = SystemClock()
    mongo = MongoClientFactory(settings)
    broker = AngelOneStackFactory(settings, clock, AsyncioSleeper(), RandomJitter()).build()
    try:
        database = mongo.database()
        instruments = await _instrument_cache(mongo, database)
        calendar = await StoredTradingCalendar.from_store(MongoCalendarStore(database))
        placement = RetentionPlacement(clock)
        repository = CandleRepository(MongoCandleStore(database), cold_archive(settings), placement)
        rejected = BarRejections()
        fetcher = DerivedBarBackfill(
            AngelOneCandleBackfill(AngelOneApi(broker.transport), rejected),
            repository,
            SessionWindow(calendar=calendar),
            keep,
            coverage=ObjectCoverageStore(cold_object_store(settings)),
            clock=clock,
        )
        yield BarFetchRuntime(fetcher, instruments, rejected)
    finally:
        try:
            await broker.sessions.logout()
        finally:
            await broker.aclose()
            await mongo.close()


@asynccontextmanager
async def open_history_runtime(settings: Settings) -> AsyncIterator[HistoryRuntime]:
    clock = SystemClock()
    mongo = MongoClientFactory(settings)
    broker = AngelOneStackFactory(settings, clock, AsyncioSleeper(), RandomJitter()).build()
    try:
        database = mongo.database()
        instruments = await _instrument_cache(mongo, database)
        calendar_store = MongoCalendarStore(database)
        cold = cold_archive(settings)
        hot = MongoCandleStore(database)
        placement = RetentionPlacement(clock)
        stack = HistoryComposer(
            source=AngelOneCandleBackfill(AngelOneApi(broker.transport)),
            repository=CandleRepository(hot, cold, placement),
            rollup=CandleRollup(hot, cold, placement, LogAlertSink()),
            coverage=MongoCoverageStore(database),
            calendar_store=calendar_store,
            calendar=await StoredTradingCalendar.from_store(calendar_store),
            clock=clock,
            alerts=LogAlertSink(),
        ).build()
        yield HistoryRuntime(stack, instruments)
    finally:
        try:
            await broker.sessions.logout()
        finally:
            await broker.aclose()
            await mongo.close()
