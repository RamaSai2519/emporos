"""Builds the REAL dependencies for the history commands from `Settings` and tears them down.

Only the CLI composition root may do this: it opens Mongo and S3 and logs in to Angel One (which
supersedes any other session for the client code — see `broker.angelone.session`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.factory import AngelOneStackFactory
from emporos.broker.backoff import RandomJitter
from emporos.cli.history_composition import HistoryComposer, HistoryStack
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.history.calendar import StoredTradingCalendar
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.store import MongoInstrumentMasterStore
from emporos.persistence.calendar_store import MongoCalendarStore
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candle_rollup import CandleRollup
from emporos.persistence.candles import CandleRepository
from emporos.persistence.collections import Collection
from emporos.persistence.coverage import MongoCoverageStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.object_store import S3ClientFactory
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


@asynccontextmanager
async def open_history_runtime(settings: Settings) -> AsyncIterator[HistoryRuntime]:
    clock = SystemClock()
    mongo = MongoClientFactory(settings)
    broker = AngelOneStackFactory(settings, clock, AsyncioSleeper(), RandomJitter()).build()
    try:
        database = mongo.database()
        master = MongoInstrumentMasterStore(
            TransactionRunner(mongo.client),
            InstrumentRepository(database),
            InstrumentVersionRepository(database),
            StagedCollectionLoader(
                database, PLATFORM_SCHEMA.spec_for(Collection.INSTRUMENTS), InstrumentRecord
            ),
        )
        instruments = InstrumentCache()
        await instruments.load_from(master)
        calendar_store = MongoCalendarStore(database)
        cold = ParquetCandleArchive(S3ClientFactory(settings).create_store())
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
