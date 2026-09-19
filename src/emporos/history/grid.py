"""The expected shape of a trading day, and how to describe what is missing from it.

A day's expected 1m grid is every minute of the session window (375 for 09:15-15:30). The broker
does not return all of them (see `domain.coverage`), so the grid is only the *upper bound* used to
work out which minutes were requested but not returned.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from emporos.core.clock import IST
from emporos.domain.coverage import TimeRange
from emporos.marketdata.session import SessionWindow, TradingCalendar

_MINUTE = timedelta(minutes=1)


class SessionGrid:
    def __init__(self, calendar: TradingCalendar, window: SessionWindow | None = None) -> None:
        self._calendar = calendar
        self._window = window or SessionWindow(calendar=calendar)

    @property
    def window(self) -> SessionWindow:
        return self._window

    def trading_days(self, first: date, last: date) -> list[date]:
        days = (first + timedelta(days=n) for n in range((last - first).days + 1))
        return [d for d in days if self._calendar.is_trading_day(d)]

    def minutes(self, day: date) -> list[datetime]:
        """Every session minute of `day` (UTC), or none if it is not a trading day."""
        if not self._calendar.is_trading_day(day):
            return []
        start, end = self._window.open_at(day), self._window.close_at(day)
        return [start + _MINUTE * n for n in range(int((end - start) / _MINUTE))]

    def day_of(self, moment: datetime) -> date:
        return moment.astimezone(IST).date()


def compress(minutes: Iterable[datetime]) -> tuple[TimeRange, ...]:
    """Contiguous minutes -> half-open ranges."""
    ranges: list[TimeRange] = []
    for minute in sorted(set(minutes)):
        if ranges and ranges[-1].end == minute:
            ranges[-1] = TimeRange(ranges[-1].start, minute + _MINUTE)
        else:
            ranges.append(TimeRange(minute, minute + _MINUTE))
    return tuple(ranges)
