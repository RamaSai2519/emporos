"""`MongoQuarantineStore` on real Atlas (EM-177). NSE:TESTQ99 is a made-up instrument id that
never appears in the real universe, so the shared dev database is never polluted for real runs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.history.quarantine import CorporateActionQuarantine, QuarantineEntry, QuarantineSource
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.quarantine_store import MongoQuarantineStore
from emporos.persistence.schema import PLATFORM_SCHEMA

pytestmark = pytest.mark.integration

INSTRUMENT = "NSE:TESTQ99"
DAY1, DAY2 = date(2099, 3, 2), date(2099, 3, 3)
RECORDED = datetime(2026, 3, 5, 0, 0, tzinfo=UTC)


@pytest.fixture
async def store(database: AsyncDatabase[Mapping[str, Any]]) -> AsyncIterator[MongoQuarantineStore]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    try:
        yield MongoQuarantineStore(database)
    finally:
        await database[Collection.CORPORATE_ACTION_QUARANTINE].delete_many(
            {"instrument_id": INSTRUMENT}
        )


async def test_entries_round_trip_and_adding_twice_never_duplicates(
    store: MongoQuarantineStore,
) -> None:
    first = QuarantineEntry(INSTRUMENT, DAY1, "split", QuarantineSource.DETECTED, RECORDED)
    second = QuarantineEntry(INSTRUMENT, DAY2, "bonus", QuarantineSource.DETECTED, RECORDED)

    await store.add([first, second])
    await store.add([second])  # re-detecting the same day upserts, not duplicates

    loaded = await store.load_all()
    mine = [e for e in loaded if e.instrument_id == INSTRUMENT]

    assert sorted(mine, key=lambda e: e.day) == [first, second]


async def test_a_curated_entry_overrides_a_detected_one_for_the_same_day(
    store: MongoQuarantineStore,
) -> None:
    detected = QuarantineEntry(INSTRUMENT, DAY1, "split", QuarantineSource.DETECTED, RECORDED)
    curated = QuarantineEntry(
        INSTRUMENT, DAY1, "confirmed split", QuarantineSource.CURATED, RECORDED
    )

    await store.add([detected])
    await store.add([curated])

    loaded = await store.load_all()
    (mine,) = (e for e in loaded if e.instrument_id == INSTRUMENT)
    assert mine.source is QuarantineSource.CURATED

    quarantine = CorporateActionQuarantine(loaded)
    assert quarantine.is_quarantined(INSTRUMENT, DAY1)
