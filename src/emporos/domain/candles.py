"""OHLCV bars. Prices are `Money`; timestamps are timezone-aware UTC (plan.md §6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from emporos.domain.money import Money


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"


@dataclass(frozen=True)
class Candle:
    """One closed bar (`partial` when it spans a feed gap), keyed by (instrument, timeframe, ts)."""

    instrument_id: str
    timeframe: Timeframe
    ts: datetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume: int
    partial: bool = False

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() != UTC.utcoffset(None):
            raise ValueError("candle ts must be timezone-aware UTC")
        if self.volume < 0:
            raise ValueError("candle volume cannot be negative")
        if self.low > self.high:
            raise ValueError("candle low cannot exceed high")
        if not all(self.low <= price <= self.high for price in (self.open, self.close)):
            raise ValueError("candle open and close must lie within [low, high]")
