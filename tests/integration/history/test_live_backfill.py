"""EM-59 acceptance, LIVE: backfill real 1m history from Angel One into real Atlas + S3 (moto), with
zero gaps and zero duplicates, surviving an interruption.

Bars are relabelled to a SCRATCH instrument id before they are stored, so no real `NSE:3045` row is
ever written or deleted. The calendar is derived from the broker's own daily bars. Skipped unless
ANGELONE_* and MONGO_URL are set. One session per client code: see test_angelone_live_auth."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.factory import AngelOneStack
from emporos.core.clock import IST, SystemClock
from emporos.core.ids import IdGenerator
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.history.backfill import BackfillOrchestrator
from emporos.history.calendar import CalendarSeeder, StoredTradingCalendar
from emporos.history.gaps import GapDetector
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

pytestmark = pytest.mark.integration

REAL_SBIN = make_instrument("3045", symbol="SBIN-EQ")


class Killed(BaseException):
    """Simulates the process dying mid-backfill."""


class RelabelledLiveSource:
    """Fetches the REAL instrument from the broker but labels the bars with a scratch id, counts
    requests, and can 'kill the process' after a chosen number of fetches."""

    def __init__(self, inner: AngelOneCandleBackfill, real: Instrument) -> None:
        self._inner = inner
        self._real = real
        self.fetches = 0
        self.die_after: int | None = None

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        if self.die_after is not None and self.fetches >= self.die_after:
            raise Killed
        self.fetches += 1
        bars = await self._inner.fetch(self._real, timeframe, start, end)
        return [
            Candle(
                instrument.instrument_id, b.timeframe, b.ts, b.open, b.high, b.low, b.close,
                b.volume, b.partial,
            )
            for b in bars
        ]  # fmt: skip


@pytest.fixture
async def scratch(
    database: AsyncDatabase[Mapping[str, Any]], s3_object_store: S3ObjectStore
) -> AsyncIterator[Instrument]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    instrument = make_instrument(f"9{IdGenerator().new_ulid()[-9:]}")
    try:
        yield instrument
    finally:
        for name in (Collection.CANDLES, Collection.HISTORY_COVERAGE):
            await database[name].delete_many({"instrument_id": instrument.instrument_id})


async def test_live_backfill_is_resumable_gapless_and_duplicate_free(
    angelone_stack: AngelOneStack,
    database: AsyncDatabase[Mapping[str, Any]],
    s3_object_store: S3ObjectStore,
    scratch: Instrument,
    tmp_path: Path,
) -> None:
    live = AngelOneCandleBackfill(AngelOneApi(angelone_stack.transport))
    source = RelabelledLiveSource(live, REAL_SBIN)
    today = datetime.now(IST).date()
    first, last = today - timedelta(days=24), today - timedelta(days=1)

    class MemoryCalendar:
        def __init__(self) -> None:
            self.days: dict[date, bool] = {}

        async def load_all(self) -> dict[date, bool]:
            return dict(self.days)

        async def save(self, days: Mapping[date, bool]) -> None:
            self.days.update(days)

    memory = MemoryCalendar()
    await CalendarSeeder(live, memory).seed(REAL_SBIN, first, last)  # the broker's own calendar
    grid = SessionGrid(StoredTradingCalendar(memory.days))

    clock = SystemClock()
    hot, coverage = MongoCandleStore(database), MongoCoverageStore(database)
    cold = ParquetCandleArchive(DiskCachingObjectStore(s3_object_store, tmp_path / "cache"))
    repo = CandleRepository(hot, cold, RetentionPlacement(clock))
    orchestrator = BackfillOrchestrator(source, repo, coverage, grid, clock, chunk_days=7)
    trading_days = grid.trading_days(first, last)

    # 1) the process dies after the first chunk...
    source.die_after = 1
    with pytest.raises(Killed):
        await orchestrator.run([scratch], first, last)
    done_before_kill = len(
        await coverage.get_days(scratch.instrument_id, Timeframe.M1, first, last)
    )
    assert 0 < done_before_kill < len(trading_days)  # a checkpoint exists, the job is unfinished

    # 2) ...and a restart resumes: only the remaining chunks are fetched
    source.die_after, source.fetches = None, 0
    report = await orchestrator.run([scratch], first, last)
    assert report.ok and report.days_already_covered == done_before_kill
    assert source.fetches == report.chunks_fetched >= 1
    assert await orchestrator.plan(scratch, first, last) == []  # nothing left to fetch

    # 3) zero gaps, no unexplained empty days, zero duplicates
    assert report.empty_days == []
    assert await GapDetector(repo, coverage, grid).find(scratch.instrument_id, first, last) == []
    series = await repo.get_range(
        scratch.instrument_id, Timeframe.M1,
        datetime.combine(first, datetime.min.time(), tzinfo=UTC) - timedelta(days=1),
        datetime.now(UTC) + timedelta(days=1),
    )  # fmt: skip
    per_day = Counter(b.ts.astimezone(IST).date() for b in series)
    assert len({b.ts for b in series}) == len(series)  # zero duplicates
    assert set(per_day) == set(trading_days)  # every trading day present, no other day
    assert all(300 <= n <= 375 for n in per_day.values())  # a full session (the broker omits a few)

    # 4) a rerun does no broker work at all
    source.fetches = 0
    again = await orchestrator.run([scratch], first, last)
    assert again.chunks_fetched == 0 and source.fetches == 0
    assert await repo.get_range(
        scratch.instrument_id, Timeframe.M1,
        datetime.combine(first, datetime.min.time(), tzinfo=UTC) - timedelta(days=1),
        datetime.now(UTC) + timedelta(days=1),
    ) == series  # fmt: skip
