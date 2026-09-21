"""EM-132: the coverage ledger kept in the archive answers the same questions the Mongo one does."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from emporos.domain.candles import Timeframe
from emporos.domain.coverage import DayCoverage, TimeRange
from emporos.persistence.object_coverage import ObjectCoverageStore
from tests.support.fakes import InMemoryObjectStore

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


def day(d: date, instrument: str = "NSE:1", empty: bool = False) -> DayCoverage:
    return DayCoverage(instrument, Timeframe.M5, d, (), NOW, empty=empty)


async def test_saved_days_come_back_within_the_range_asked() -> None:
    ledger = ObjectCoverageStore(InMemoryObjectStore())
    await ledger.save([day(date(2026, 3, 30)), day(date(2026, 3, 31)), day(date(2026, 4, 1))])

    got = await ledger.get_days("NSE:1", Timeframe.M5, date(2026, 3, 31), date(2026, 4, 30))

    assert sorted(got) == [date(2026, 3, 31), date(2026, 4, 1)]  # across a month boundary


async def test_a_day_saved_again_replaces_its_own_entry_and_nothing_else() -> None:
    ledger = ObjectCoverageStore(InMemoryObjectStore())
    await ledger.save([day(date(2026, 3, 2)), day(date(2026, 3, 3))])

    await ledger.save([day(date(2026, 3, 2), empty=True)])

    got = await ledger.get_days("NSE:1", Timeframe.M5, date(2026, 3, 1), date(2026, 3, 31))
    assert got[date(2026, 3, 2)].empty and not got[date(2026, 3, 3)].empty


async def test_instruments_and_timeframes_keep_separate_ledgers() -> None:
    store = InMemoryObjectStore()
    ledger = ObjectCoverageStore(store)
    await ledger.save([day(date(2026, 3, 2)), day(date(2026, 3, 2), instrument="NSE:2")])

    assert list(await ledger.get_days("NSE:1", Timeframe.M5, date(2026, 3, 1), date(2026, 3, 9)))
    assert not await ledger.get_days("NSE:1", Timeframe.M1, date(2026, 3, 1), date(2026, 3, 9))
    assert not await ledger.get_days("NSE:3", Timeframe.M5, date(2026, 3, 1), date(2026, 3, 9))


async def test_absent_ranges_and_times_survive_a_round_trip() -> None:
    ledger = ObjectCoverageStore(InMemoryObjectStore())
    absent = (TimeRange(NOW, NOW + timedelta(minutes=3)),)
    await ledger.save([DayCoverage("NSE:1", Timeframe.M1, date(2026, 3, 2), absent, NOW, False)])

    (back,) = (
        await ledger.get_days("NSE:1", Timeframe.M1, date(2026, 3, 2), date(2026, 3, 2))
    ).values()

    assert back.absent == absent and back.fetched_at == NOW and back.complete is False


async def test_one_small_object_per_month_not_one_per_day() -> None:
    store = InMemoryObjectStore()
    ledger = ObjectCoverageStore(store)

    await ledger.save([day(date(2026, 3, n)) for n in range(2, 28)])

    assert len(await store.list_objects("coverage/")) == 1
