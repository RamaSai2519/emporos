"""What the history layer needs from a broker, defined here (consumer-owned Protocols)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.coverage import DayCoverage
from emporos.domain.instruments import Instrument


class HistoricalCandleSource(Protocol):
    """Candles for `[start, end)`. Callers must keep the span within the source's per-request limit:
    Angel One SILENTLY truncates longer ranges to the most recent 30 days (recorded live), so an
    oversized request looks like missing data rather than an error."""

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...


class CoverageStore(Protocol):
    async def get_days(
        self, instrument_id: str, timeframe: Timeframe, first: date, last: date
    ) -> dict[date, DayCoverage]: ...

    async def save(self, coverages: list[DayCoverage]) -> None: ...
