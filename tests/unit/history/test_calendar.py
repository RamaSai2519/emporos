"""EM-56: the trading calendar — stored, defaulted, and derived from the broker's daily bars."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime

import pytest

from emporos.broker.errors import BrokerRateLimitedError
from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money
from emporos.history.calendar import (
    CalendarSeeder,
    StoredTradingCalendar,
    derive_trading_days,
)
from tests.support.fakes import make_instrument

SBIN = make_instrument("3045")
FIRST, LAST = date(2026, 9, 11), date(2026, 9, 15)
MON_HOLIDAY = date(2026, 9, 14)  # recorded live: the broker has no daily bar for this Monday


def daily(day: date) -> Candle:
    p = Money.of("100")
    ts = datetime(day.year, day.month, day.day, 0, 0, tzinfo=IST).astimezone(UTC)
    return Candle("NSE:3045", Timeframe.D1, ts, p, p, p, p, 1)


class DailySource:
    def __init__(self, traded: list[date]) -> None:
        self._traded = traded
        self.calls: list[tuple[Timeframe, datetime, datetime]] = []

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.calls.append((timeframe, start, end))
        return [daily(d) for d in self._traded]


class MemoryCalendarStore:
    def __init__(self, days: Mapping[date, bool] | None = None) -> None:
        self.days = dict(days or {})

    async def load_all(self) -> dict[date, bool]:
        return dict(self.days)

    async def save(self, days: Mapping[date, bool]) -> None:
        self.days |= days


def test_weekends_are_never_trading_days_and_unknown_weekdays_default_to_trading() -> None:
    cal = StoredTradingCalendar()
    assert cal.is_trading_day(date(2026, 9, 18)) and cal.is_trading_day(date(2027, 3, 1))
    assert not cal.is_trading_day(date(2026, 9, 19)) and not cal.is_trading_day(date(2026, 9, 20))


def test_a_stored_holiday_overrides_the_weekday_default_but_a_weekend_record_does_not() -> None:
    cal = StoredTradingCalendar({MON_HOLIDAY: False, date(2026, 9, 19): True})
    assert not cal.is_trading_day(MON_HOLIDAY)
    assert not cal.is_trading_day(date(2026, 9, 19))  # a stray "Saturday trades" record is ignored


async def test_the_calendar_loads_and_refreshes_from_its_store() -> None:
    store = MemoryCalendarStore({MON_HOLIDAY: False})
    cal = await StoredTradingCalendar.from_store(store)
    assert not cal.is_trading_day(MON_HOLIDAY) and cal.known_days() == 1

    store.days[date(2026, 9, 15)] = False
    await cal.refresh(store)
    assert not cal.is_trading_day(date(2026, 9, 15))


def test_trading_days_are_derived_from_the_brokers_daily_bars() -> None:
    traded = [date(2026, 9, 11), date(2026, 9, 15), date(2026, 9, 16)]  # no 09-14, no weekend
    days = derive_trading_days([daily(d) for d in traded], date(2026, 9, 11), date(2026, 9, 17))

    assert days == {
        date(2026, 9, 11): True,
        date(2026, 9, 14): False,  # a weekday with no bar: not a trading day
        date(2026, 9, 15): True,
        date(2026, 9, 16): True,
        date(2026, 9, 17): False,  # nothing reported (yet): treated as not traded
    }
    assert date(2026, 9, 12) not in days  # weekends are not recorded at all


async def test_seeding_stores_the_derived_calendar() -> None:
    source = DailySource([date(2026, 9, 11), date(2026, 9, 15)])
    store = MemoryCalendarStore()

    days = await CalendarSeeder(source, store).seed(SBIN, date(2026, 9, 11), date(2026, 9, 15))

    assert (
        store.days
        == days
        == {
            date(2026, 9, 11): True,
            date(2026, 9, 14): False,
            date(2026, 9, 15): True,
        }
    )
    assert source.calls[0][0] is Timeframe.D1


async def test_a_reference_with_no_daily_bars_is_refused_not_stored_as_all_holidays() -> None:
    store = MemoryCalendarStore()
    with pytest.raises(ConfigurationError):
        await CalendarSeeder(DailySource([]), store).seed(
            SBIN, date(2026, 9, 11), date(2026, 9, 15)
        )
    assert store.days == {}


async def test_a_source_failure_propagates_and_stores_nothing() -> None:
    class Failing:
        async def fetch(self, *args: object) -> list[Candle]:
            raise BrokerRateLimitedError("denied")

    store = MemoryCalendarStore()
    with pytest.raises(BrokerRateLimitedError):
        await CalendarSeeder(Failing(), store).seed(SBIN, FIRST, LAST)  # type: ignore[arg-type]
    assert store.days == {}
