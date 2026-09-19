"""The session window: 09:15-15:30 IST, half-open, weekdays (holidays via the calendar)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from emporos.core.clock import IST
from emporos.marketdata.session import SessionWindow

FRIDAY = date(2026, 9, 18)
SATURDAY = date(2026, 9, 19)


def ist(day: date, hour: int, minute: int, second: int = 0, micro: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, micro, tzinfo=IST)


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        (ist(FRIDAY, 9, 14, 59, 999_999), False),
        (ist(FRIDAY, 9, 15), True),  # the open is inclusive
        (ist(FRIDAY, 12, 0), True),
        (ist(FRIDAY, 15, 29, 59, 999_999), True),
        (ist(FRIDAY, 15, 30), False),  # the close is exclusive
        (ist(FRIDAY, 20, 0), False),
        (ist(SATURDAY, 10, 0), False),  # weekend
    ],
)
def test_the_regular_session_is_half_open_and_weekday_only(moment: datetime, inside: bool) -> None:
    assert SessionWindow().contains(moment) is inside


def test_a_utc_timestamp_is_judged_on_the_ist_clock() -> None:
    assert SessionWindow().contains(datetime(2026, 9, 18, 3, 45, tzinfo=UTC))  # 09:15 IST
    assert not SessionWindow().contains(datetime(2026, 9, 18, 3, 44, 59, tzinfo=UTC))  # 09:14:59


def test_pre_open_is_ignored_unless_explicitly_enabled() -> None:
    pre_open = ist(FRIDAY, 9, 5)
    assert not SessionWindow().contains(pre_open)
    assert SessionWindow.with_pre_open().contains(pre_open)
    assert not SessionWindow.with_pre_open().contains(ist(FRIDAY, 8, 59))


def test_holidays_come_from_the_injected_calendar() -> None:
    class Holiday:
        def is_trading_day(self, day: date) -> bool:
            return day != FRIDAY

    assert not SessionWindow(calendar=Holiday()).contains(ist(FRIDAY, 10, 0))
    assert SessionWindow(calendar=Holiday()).contains(ist(date(2026, 9, 17), 10, 0))


def test_open_close_and_next_trading_day_helpers() -> None:
    window = SessionWindow()
    assert window.open_at(FRIDAY) == ist(FRIDAY, 9, 15)
    assert window.open_at(FRIDAY).utcoffset() == timedelta(0)  # UTC, never IST-zoned
    assert window.close_at(FRIDAY) == ist(FRIDAY, 15, 30)
    assert window.next_day(FRIDAY) == date(2026, 9, 21)  # skips the weekend
