"""A scripted broker-history source and a session-shaped bar generator for history tests."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta

from emporos.broker.errors import BrokerRateLimitedError
from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money
from emporos.marketdata.session import SessionWindow

_MINUTE = timedelta(minutes=1)


def bar(instrument: Instrument, ts: datetime, price: str = "100.00", volume: int = 10) -> Candle:
    p = Money.of(price)
    return Candle(instrument.instrument_id, Timeframe.M1, ts, p, p, p, p, volume)


class BrokerHistory:
    """Behaves like the recorded broker: every session minute of every weekday except the minutes
    in `silent` (the real API omits ~14 a day even for SBIN). Records each request and can fail
    a chosen request number the way the upstream defect does."""

    def __init__(self, silent: Callable[[datetime], bool] | None = None) -> None:
        self.requests: list[tuple[str, datetime, datetime]] = []
        self._silent = silent or (lambda ts: False)
        self.fail_requests: set[int] = set()
        self.window = SessionWindow()

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.requests.append((instrument.instrument_id, start, end))
        if len(self.requests) in self.fail_requests:
            raise BrokerRateLimitedError("Access denied because of exceeding access rate")
        if (end - start) > timedelta(days=30):  # the real API silently truncates: behave like it
            start = end - timedelta(days=30)
        bars = []
        minute = start
        while minute < end:
            if self.window.contains(minute) and not self._silent(minute):
                bars.append(bar(instrument, minute, f"{100 + minute.minute / 100:.2f}"))
            minute += _MINUTE
        return bars


def session_minutes(day: date, window: SessionWindow | None = None) -> list[datetime]:
    w = window or SessionWindow()
    start = w.open_at(day)
    return [start + _MINUTE * i for i in range(375)]


def ist_day(offset: int, base: date = date(2026, 9, 18)) -> date:
    return base + timedelta(days=offset)


def all_days(first: date, last: date) -> Sequence[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


IST_ZONE = IST
