"""`MongoCoverageStore` against the real Atlas index: round trip, idempotent upsert, range reads."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.domain.candles import Timeframe
from emporos.domain.coverage import DayCoverage, TimeRange
from emporos.persistence.collections import Collection
from emporos.persistence.coverage import MongoCoverageStore
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 14, 9, 45, tzinfo=UTC)


@pytest.fixture
async def rig(
    database: AsyncDatabase[Mapping[str, Any]],
) -> AsyncIterator[tuple[MongoCoverageStore, str]]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    instrument_id = f"it-{IdGenerator().new_ulid()}"
    try:
        yield MongoCoverageStore(database), instrument_id
    finally:
        await database[Collection.HISTORY_COVERAGE].delete_many({"instrument_id": instrument_id})


def coverage(instrument_id: str, day: date, **overrides: Any) -> DayCoverage:
    fields: dict[str, Any] = {
        "instrument_id": instrument_id,
        "timeframe": Timeframe.M1,
        "day": day,
        "absent": (TimeRange(T0, T0 + timedelta(minutes=14)),),
        "fetched_at": T0,
    }
    return DayCoverage(**{**fields, **overrides})


async def test_coverage_round_trips_exactly(rig: tuple[MongoCoverageStore, str]) -> None:
    store, instrument_id = rig
    saved = coverage(instrument_id, date(2026, 9, 14), complete=False, empty=True)

    await store.save([saved])

    assert await store.get_days(
        instrument_id, Timeframe.M1, date(2026, 9, 14), date(2026, 9, 14)
    ) == {date(2026, 9, 14): saved}


async def test_saving_the_same_day_twice_replaces_it_and_never_duplicates(
    rig: tuple[MongoCoverageStore, str],
) -> None:
    store, instrument_id = rig
    day = date(2026, 9, 14)
    await store.save([coverage(instrument_id, day, absent=())])
    await store.save([coverage(instrument_id, day)])  # a later fetch found absent minutes

    found = await store.get_days(instrument_id, Timeframe.M1, day, day)

    assert list(found) == [day] and len(found[day].absent) == 1


async def test_range_reads_are_bounded_by_instrument_timeframe_and_day(
    rig: tuple[MongoCoverageStore, str],
) -> None:
    store, instrument_id = rig
    await store.save([coverage(instrument_id, date(2026, 9, d)) for d in (14, 15, 16)])
    await store.save([coverage(instrument_id, date(2026, 9, 15), timeframe=Timeframe.M5)])

    found = await store.get_days(instrument_id, Timeframe.M1, date(2026, 9, 15), date(2026, 9, 16))

    assert sorted(found) == [date(2026, 9, 15), date(2026, 9, 16)]
    assert (
        await store.get_days("someone-else", Timeframe.M1, date(2026, 9, 1), date(2026, 9, 30))
        == {}
    )
    await store.save([])  # saving nothing is a no-op
