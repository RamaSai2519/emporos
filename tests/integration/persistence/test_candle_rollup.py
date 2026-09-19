"""EM-58 on real Atlas + S3: rolled-up bars read back identically through `CandleRepository`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candle_rollup import CandleRollup
from emporos.persistence.candles import CandleRepository
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.object_cache import DiskCachingObjectStore
from emporos.persistence.object_store import S3ObjectStore
from emporos.persistence.placement import RetentionPlacement
from emporos.persistence.schema import PLATFORM_SCHEMA

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
START, END = NOW - timedelta(days=400), NOW + timedelta(days=1)


@dataclass
class Rig:
    instrument_id: str
    hot: MongoCandleStore
    cold: ParquetCandleArchive
    repo: CandleRepository
    rollup: CandleRollup


def bar(instrument: str, age_days: int) -> Candle:
    p = Money.of(f"{100 + age_days / 100:.2f}")
    ts = (NOW - timedelta(days=age_days)).replace(second=0, microsecond=0)
    return Candle(instrument, Timeframe.M1, ts, p, p, p, p, age_days)


@pytest.fixture
async def rig(
    database: AsyncDatabase[Mapping[str, Any]], s3_object_store: S3ObjectStore, tmp_path: Path
) -> AsyncIterator[Rig]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    instrument_id = f"it-{IdGenerator().new_ulid()}"
    hot = MongoCandleStore(database)
    cold = ParquetCandleArchive(DiskCachingObjectStore(s3_object_store, tmp_path / "cache"))
    rollup = CandleRollup(hot, cold, RetentionPlacement(FixedClock(NOW)))
    try:
        yield Rig(instrument_id, hot, cold, CandleRepository(hot, cold), rollup)
    finally:
        await database[Collection.CANDLES].delete_many({"instrument_id": instrument_id})


async def test_the_same_range_reads_identically_before_and_after_the_rollup(rig: Rig) -> None:
    bars = [bar(rig.instrument_id, age) for age in (380, 200, 95, 91, 89, 45, 3)]
    await rig.hot.upsert(bars)
    before = await rig.repo.get_range(rig.instrument_id, Timeframe.M1, START, END)

    report = await rig.rollup.run([Timeframe.M1], [rig.instrument_id])

    after = await rig.repo.get_range(rig.instrument_id, Timeframe.M1, START, END)
    assert report.ok and report.archived == 4  # the four older than the 90-day retention
    assert after == before == sorted(bars, key=lambda b: b.ts)  # exact, Decimal128 round trip
    remaining = await rig.hot.read(rig.instrument_id, Timeframe.M1, START, END)
    assert all(b.ts >= NOW - timedelta(days=90) for b in remaining) and len(remaining) == 3
    assert len(await rig.cold.read(rig.instrument_id, Timeframe.M1, START, END)) == 4


async def test_running_it_again_is_harmless(rig: Rig) -> None:
    await rig.hot.upsert([bar(rig.instrument_id, 200), bar(rig.instrument_id, 5)])
    await rig.rollup.run([Timeframe.M1], [rig.instrument_id])
    before = await rig.repo.get_range(rig.instrument_id, Timeframe.M1, START, END)

    again = await rig.rollup.run([Timeframe.M1], [rig.instrument_id])

    assert again.ok and again.deleted == 0
    assert await rig.repo.get_range(rig.instrument_id, Timeframe.M1, START, END) == before
