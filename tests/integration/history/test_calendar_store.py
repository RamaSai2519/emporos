"""`MongoCalendarStore` on real Atlas (dates in 2099 so the shared calendar is never touched),
and the calendar derived from the LIVE broker's daily bars."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import date
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.factory import AngelOneStack
from emporos.history.calendar import CalendarSeeder, StoredTradingCalendar
from emporos.persistence.calendar_store import MongoCalendarStore
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA
from tests.support.fakes import make_instrument

pytestmark = pytest.mark.integration

FAR = [date(2099, 3, 2), date(2099, 3, 3), date(2099, 3, 4)]


@pytest.fixture
async def store(database: AsyncDatabase[Mapping[str, Any]]) -> AsyncIterator[MongoCalendarStore]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    try:
        yield MongoCalendarStore(database)
    finally:
        await database[Collection.MARKET_CALENDAR].delete_many(
            {"date": {"$in": [d.isoformat() for d in FAR]}}
        )


async def test_calendar_records_round_trip_and_saving_twice_never_duplicates(
    store: MongoCalendarStore,
) -> None:
    await store.save({FAR[0]: True, FAR[1]: False})
    await store.save(
        {FAR[1]: False, FAR[2]: True}
    )  # overlaps: an upsert, not a duplicate key error

    loaded = await store.load_all()

    assert {d: loaded[d] for d in FAR} == {FAR[0]: True, FAR[1]: False, FAR[2]: True}
    calendar = await StoredTradingCalendar.from_store(store)
    assert not calendar.is_trading_day(FAR[1]) and calendar.is_trading_day(FAR[0])


async def test_the_calendar_derived_from_the_real_brokers_daily_bars_finds_the_holiday(
    angelone_stack: AngelOneStack,
) -> None:
    class MemoryStore:
        def __init__(self) -> None:
            self.days: dict[date, bool] = {}

        async def load_all(self) -> dict[date, bool]:
            return dict(self.days)

        async def save(self, days: Mapping[date, bool]) -> None:
            self.days.update(days)

    source = AngelOneCandleBackfill(AngelOneApi(angelone_stack.transport))
    memory = MemoryStore()

    derived = await CalendarSeeder(source, memory).seed(  # type: ignore[arg-type]
        make_instrument("3045", symbol="SBIN-EQ"), date(2026, 9, 1), date(2026, 9, 18)
    )

    weekdays_traded = sum(derived.values())
    monday_is_a_holiday = derived[date(2026, 9, 14)] is False  # recorded live: no daily bar
    friday_traded = derived[date(2026, 9, 18)] is True
    assert monday_is_a_holiday
    assert friday_traded
    assert 10 <= weekdays_traded < len(derived)  # most weekdays trade, but not all
