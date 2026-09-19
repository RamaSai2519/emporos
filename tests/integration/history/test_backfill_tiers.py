"""EM-57 on real Atlas + a real S3 endpoint: a backfill straddling the 90-day boundary lands recent
1m bars in Mongo and older ones in S3 Parquet, and `CandleRepository` reads one identical series."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.candles import Timeframe
from emporos.history.backfill import BackfillOrchestrator
from emporos.history.calendar import StoredTradingCalendar
from emporos.history.grid import SessionGrid
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleRepository
from emporos.persistence.collections import Collection
from emporos.persistence.coverage import MongoCoverageStore
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.object_cache import DiskCachingObjectStore
from emporos.persistence.object_store import S3ObjectStore
from emporos.persistence.placement import RetentionPlacement
from emporos.persistence.schema import PLATFORM_SCHEMA
from tests.support.fakes import make_instrument
from tests.support.history import BrokerHistory

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=90)  # 2026-06-21
FIRST, LAST = date(2026, 6, 15), date(2026, 7, 1)  # straddles the cutoff; 13 weekdays


@dataclass
class Rig:
    instrument_id: str
    repo: CandleRepository
    hot: MongoCandleStore
    cold: ParquetCandleArchive
    orchestrator: BackfillOrchestrator
    coverage: MongoCoverageStore
    history: BrokerHistory


@pytest.fixture
async def rig(
    database: AsyncDatabase[Mapping[str, Any]], s3_object_store: S3ObjectStore, tmp_path: Path
) -> AsyncIterator[Rig]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    token = f"9{IdGenerator().new_ulid()[-8:]}".replace("_", "0")
    instrument = make_instrument(token)
    hot, coverage = MongoCandleStore(database), MongoCoverageStore(database)
    cold = ParquetCandleArchive(DiskCachingObjectStore(s3_object_store, tmp_path / "cache"))
    clock = FixedClock(NOW)
    repo = CandleRepository(hot, cold, RetentionPlacement(clock))
    history = BrokerHistory()
    orchestrator = BackfillOrchestrator(
        history, repo, coverage, SessionGrid(StoredTradingCalendar()), clock
    )
    rig_ = Rig(instrument.instrument_id, repo, hot, cold, orchestrator, coverage, history)
    rig_.instrument = instrument  # type: ignore[attr-defined]
    try:
        yield rig_
    finally:
        for name in (Collection.CANDLES, Collection.HISTORY_COVERAGE):
            await database[name].delete_many({"instrument_id": instrument.instrument_id})


async def test_backfill_lands_each_bar_in_its_tier_and_reads_back_as_one_series(rig: Rig) -> None:
    instrument = rig.instrument  # type: ignore[attr-defined]
    report = await rig.orchestrator.run([instrument], FIRST, LAST)
    assert report.ok

    start, end = datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 5, tzinfo=UTC)
    hot = await rig.hot.read(rig.instrument_id, Timeframe.M1, start, end)
    cold = await rig.cold.read(rig.instrument_id, Timeframe.M1, start, end)
    union = await rig.repo.get_range(rig.instrument_id, Timeframe.M1, start, end)

    assert hot and cold  # the run straddled the boundary
    assert min(b.ts for b in hot) >= CUTOFF and max(b.ts for b in cold) < CUTOFF
    assert not {b.ts for b in hot} & {b.ts for b in cold}  # each bar lives in exactly one tier
    assert len(union) == 13 * 375 == len(hot) + len(cold)
    assert [b.ts for b in union] == sorted(b.ts for b in union)  # one ordered series


async def test_rerunning_the_backfill_changes_nothing_in_either_tier(rig: Rig) -> None:
    instrument = rig.instrument  # type: ignore[attr-defined]
    start, end = datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 5, tzinfo=UTC)
    await rig.orchestrator.run([instrument], FIRST, LAST)
    before = await rig.repo.get_range(rig.instrument_id, Timeframe.M1, start, end)
    requests = len(rig.history.requests)

    again = await rig.orchestrator.run([instrument], FIRST, LAST)

    assert again.chunks_fetched == 0 and len(rig.history.requests) == requests  # resumable skip
    assert await rig.repo.get_range(rig.instrument_id, Timeframe.M1, start, end) == before


async def test_the_series_equals_what_an_all_hot_write_would_have_produced(rig: Rig) -> None:
    instrument = rig.instrument  # type: ignore[attr-defined]
    await rig.orchestrator.run([instrument], FIRST, LAST)
    start, end = datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 5, tzinfo=UTC)

    union = await rig.repo.get_range(rig.instrument_id, Timeframe.M1, start, end)

    expected = await BrokerHistory().fetch(
        instrument, Timeframe.M1, SessionGrid(StoredTradingCalendar()).window.open_at(FIRST),
        SessionGrid(StoredTradingCalendar()).window.close_at(LAST),
    )  # fmt: skip
    assert union == expected
