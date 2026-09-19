"""EM-52 against the real Atlas unique index: a whole simulated session, aggregated and
persisted through `CandleRepository.upsert` (the only allowed path), twice: no duplicates,
exact round trip."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.clock import IST, FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.candles import Candle, Timeframe
from emporos.marketdata.aggregator import MinuteCandleAggregator
from emporos.marketdata.candle_writer import CandlePersister
from emporos.marketdata.timeframes import TimeframeDeriver, derive
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleRepository
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.object_cache import DiskCachingObjectStore
from emporos.persistence.object_store import S3ObjectStore
from emporos.persistence.schema import PLATFORM_SCHEMA
from tests.support.fakes import CandleCollector, make_tick

pytestmark = pytest.mark.integration

SESSION_DAY = date(2026, 9, 18)


def ist(hour: int, minute: int, second: int = 0) -> datetime:
    local = datetime(2026, 9, 18, hour, minute, second, tzinfo=IST)
    return local.astimezone(UTC)


@pytest.fixture
async def repository(
    database: AsyncDatabase[Mapping[str, Any]], s3_object_store: S3ObjectStore, tmp_path: Path
) -> AsyncIterator[tuple[CandleRepository, str]]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    instrument_id = f"it-{IdGenerator().new_ulid()}"
    cold = ParquetCandleArchive(DiskCachingObjectStore(s3_object_store, tmp_path / "cache"))
    try:
        yield CandleRepository(MongoCandleStore(database), cold), instrument_id
    finally:
        await database[Collection.CANDLES].delete_many({"instrument_id": instrument_id})


async def run_session(repository: CandleRepository, instrument_id: str) -> list[Candle]:
    """Ticks in a thin, gappy session; bars closed minute by minute; everything persisted."""
    clock = FixedClock(ist(9, 0))
    aggregator = MinuteCandleAggregator(clock)
    deriver, persister, collector = (
        TimeframeDeriver(),
        CandlePersister(repository),
        CandleCollector(),
    )
    for sink in (persister, collector, deriver):
        aggregator.subscribe(sink)
    deriver.subscribe(persister)
    deriver.subscribe(collector)

    ticks = {ist(9, 15, 3): "100.00", ist(9, 15, 40): "100.60", ist(9, 22, 10): "101.20",
             ist(10, 5, 5): "99.80", ist(13, 44, 50): "102.05"}  # fmt: skip
    cumulative = 0
    minute = ist(9, 15)
    while minute < ist(15, 30):
        for at, price in ticks.items():
            if minute <= at < minute + timedelta(minutes=1):
                cumulative += 500
                clock.set(max(clock.now(), at))
                aggregator.on_tick(
                    make_tick(at, price, volume=cumulative, instrument_id=instrument_id)
                )
        minute += timedelta(minutes=1)
        clock.set(minute + timedelta(seconds=1))
        aggregator.advance()
        await persister.flush()
    return collector.candles


async def stored(
    repository: CandleRepository, instrument_id: str, timeframe: Timeframe
) -> list[Candle]:
    return await repository.get_range(instrument_id, timeframe, ist(9, 0), ist(16, 0))


async def test_a_whole_session_persists_exactly_once_however_often_it_is_replayed(
    repository: tuple[CandleRepository, str],
) -> None:
    repo, instrument_id = repository

    first_run = await run_session(repo, instrument_id)
    after_first = {tf: await stored(repo, instrument_id, tf) for tf in (Timeframe.M1, Timeframe.M5)}
    second_run = await run_session(repo, instrument_id)  # a restart replaying the same session
    after_second = {
        tf: await stored(repo, instrument_id, tf) for tf in (Timeframe.M1, Timeframe.M5)
    }

    assert first_run == second_run  # deterministic
    assert after_first == after_second  # replay changed nothing: no duplicates, no drift
    assert len(after_second[Timeframe.M1]) == 375  # one bar per session minute, silent ones flat
    assert len(after_second[Timeframe.M5]) == 75


async def test_what_is_stored_round_trips_exactly_and_higher_timeframes_match_the_minutes(
    repository: tuple[CandleRepository, str],
) -> None:
    repo, instrument_id = repository
    emitted = await run_session(repo, instrument_id)

    minutes = await stored(repo, instrument_id, Timeframe.M1)
    assert minutes == [c for c in emitted if c.timeframe is Timeframe.M1]  # Decimal128 round trip
    for timeframe in (Timeframe.M5, Timeframe.M15, Timeframe.H1):
        expected = [c for c in derive(minutes, [timeframe])]
        assert await stored(repo, instrument_id, timeframe) == expected
    assert minutes[0].partial is False and minutes[0].volume == 1_000  # baseline from the open
