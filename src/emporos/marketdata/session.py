"""The trading session window (plan.md §7 "Session handling").

Regular session is 09:15-15:30 IST, half-open: a tick stamped exactly 15:30:00 belongs to
the next (closed) minute, so the last bar is 15:29-15:30. Pre-open (09:00-09:15) is ignored
unless explicitly enabled. Holidays come from a `TradingCalendar`; until Phase 13 supplies the
real one (from `market_calendar`), the default treats every weekday as a trading day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from emporos.core.clock import IST

REGULAR_OPEN = time(9, 15)
PRE_OPEN = time(9, 0)
REGULAR_CLOSE = time(15, 30)


class TradingCalendar(Protocol):
    def is_trading_day(self, day: date) -> bool: ...


class WeekdayCalendar:
    """Mon-Fri, no holidays. A stand-in until the real exchange calendar is wired (Phase 13)."""

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5


@dataclass(frozen=True)
class SessionWindow:
    open_time: time = REGULAR_OPEN
    close_time: time = REGULAR_CLOSE
    calendar: TradingCalendar = field(default_factory=WeekdayCalendar)

    @classmethod
    def with_pre_open(cls, calendar: TradingCalendar | None = None) -> SessionWindow:
        return cls(open_time=PRE_OPEN, calendar=calendar or WeekdayCalendar())

    def contains(self, moment: datetime) -> bool:
        local = moment.astimezone(IST)
        return self.calendar.is_trading_day(local.date()) and (
            self.open_time <= local.time() < self.close_time
        )

    def open_at(self, day: date) -> datetime:
        """The session open on the IST date `day`, as UTC (all timestamps in emporos are UTC)."""
        return datetime.combine(day, self.open_time, tzinfo=IST).astimezone(UTC)

    def close_at(self, day: date) -> datetime:
        return datetime.combine(day, self.close_time, tzinfo=IST).astimezone(UTC)

    def next_day(self, day: date) -> date:
        following = day + timedelta(days=1)
        while not self.calendar.is_trading_day(following):
            following += timedelta(days=1)
        return following
