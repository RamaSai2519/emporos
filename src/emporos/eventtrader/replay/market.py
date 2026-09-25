"""The bars the replay reads (EM-240). The Protocol is what the fills and the engine depend on; the
production implementation reads the vaulted local bars, the tests use a dictionary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from emporos.domain.candles import Candle

__all__ = ["DailyBar", "MarketData"]


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("a daily bar's open and close lie within its range")


class MarketData(Protocol):
    def five_minute_bars(self, instrument_id: str, day: date) -> Sequence[Candle]:
        """One session's 5-minute bars, oldest first (empty when the name did not trade)."""
        ...

    def daily_bars(self, instrument_id: str, after: date, count: int) -> Sequence[DailyBar]:
        """The `count` sessions AFTER `after`, oldest first (fewer at the end of the data)."""
        ...

    def next_session(self, day: date) -> date | None:
        """The first trading session strictly after `day`, or None past the end of the data."""
        ...

    def is_session(self, day: date) -> bool: ...

    def last_close(self, instrument_id: str, at: datetime) -> Decimal | None:
        """The close of the last 5-minute bar completed at or before `at` (looking back over
        earlier sessions if `at` precedes the day's first bar), or None if there is none."""
        ...
