"""`CandleRepository` against the real Atlas hot tier and a real S3 HTTP endpoint (EM-23/EM-24):
one identical bar series whether a range is served from Mongo, from S3, or spans both."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleRepository
from emporos.persistence.collections import Collection
from emporos.persistence.errors import DuplicateKeyTranslator, DuplicateRecordError
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.object_cache import DiskCachingObjectStore
from emporos.persistence.object_store import S3ObjectStore
from emporos.persistence.schema import PLATFORM_SCHEMA
from tests.support.candles import at, minute_series

pytestmark = pytest.mark.integration


@dataclass
class Rig:
    instrument_id: str
    hot: MongoCandleStore
    cold: ParquetCandleArchive
    bars: list[Candle]

    def repository(self) -> CandleRepository:
        return CandleRepository(self.hot, self.cold)

    async def series(self) -> list[Candle]:
        return await self.repository().get_range(
            self.instrument_id,
            Timeframe.M1,
            self.bars[0].ts,
            self.bars[-1].ts + timedelta(seconds=1),
        )


@pytest.fixture
async def rig(
    database: AsyncDatabase[Mapping[str, Any]], s3_object_store: S3ObjectStore, tmp_path: Path
) -> AsyncIterator[Rig]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    instrument_id = f"it-{IdGenerator().new_ulid()}"
    cached = DiskCachingObjectStore(s3_object_store, tmp_path / "cache")
    # One bar per day, Jan 30 to Feb 4: the run crosses a month boundary (two Parquet partitions).
    bars = minute_series(instrument_id, at(2026, 1, 30), 6, every=timedelta(days=1))
    try:
        yield Rig(instrument_id, MongoCandleStore(database), ParquetCandleArchive(cached), bars)
    finally:
        await database[Collection.CANDLES].delete_many({"instrument_id": instrument_id})


async def test_a_range_served_only_from_mongo(rig: Rig) -> None:
    await rig.hot.upsert(rig.bars)

    assert await rig.series() == rig.bars


async def test_a_range_served_only_from_s3(rig: Rig) -> None:
    await rig.cold.archive(rig.bars)

    assert await rig.series() == rig.bars


async def test_a_range_spanning_both_tiers_is_identical(rig: Rig) -> None:
    await rig.cold.archive(rig.bars[:3])
    await rig.hot.upsert(rig.bars[3:])

    assert await rig.series() == rig.bars


async def test_the_series_is_identical_whichever_tier_serves_it(
    rig: Rig, database: AsyncDatabase[Mapping[str, Any]]
) -> None:
    await rig.hot.upsert(rig.bars)
    from_mongo = await rig.series()
    await database[Collection.CANDLES].delete_many({"instrument_id": rig.instrument_id})
    await rig.cold.archive(rig.bars)
    from_s3 = await rig.series()

    assert from_mongo == from_s3 == rig.bars


async def test_upserting_the_same_bars_twice_leaves_one_copy_each(
    rig: Rig, database: AsyncDatabase[Mapping[str, Any]]
) -> None:
    await rig.repository().upsert(rig.bars)
    await rig.repository().upsert(rig.bars)

    stored = await database[Collection.CANDLES].count_documents(
        {"instrument_id": rig.instrument_id}
    )
    assert stored == len(rig.bars)


async def test_a_raw_duplicate_candle_insert_raises_the_typed_error(
    rig: Rig, database: AsyncDatabase[Mapping[str, Any]]
) -> None:
    from pymongo.errors import DuplicateKeyError

    await rig.hot.upsert(rig.bars[:1])
    document = {"instrument_id": rig.instrument_id, "timeframe": "1m", "ts": rig.bars[0].ts}

    with pytest.raises(DuplicateKeyError) as raised:
        await database[Collection.CANDLES].insert_one(dict(document))

    error = DuplicateKeyTranslator().translate(Collection.CANDLES, raised.value)
    assert isinstance(error, DuplicateRecordError)
    assert error.key_fields == ("instrument_id", "timeframe", "ts")
