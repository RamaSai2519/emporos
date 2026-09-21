"""Which tier a candle belongs in, by age (plan.md §7 retention tiers).

    1m         -> Mongo for 3 days, then Parquet forever
    5m/15m/1h  -> Mongo for 14 days, then Parquet
    1d         -> Mongo forever (small)

Mongo holds only what the live pipeline needs (restart recovery and strategy warm-up); history is
Parquet, one file per instrument-month, in the cold tier (S3, or a local directory). A year of 5m
bars for 29 symbols is ~570k Mongo documents and ~60 MB of index, against ~16 MB of Parquet, and a
backtest reads it sequentially, which is what Parquet is for.

`CandleRepository` uses a placement policy to route WRITES (so a year of backfilled 1m history goes
straight to S3 instead of overflowing the hot tier) and the nightly rollup uses the same policy to
decide what to move. Reads are unaffected: the repository always unions both tiers.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol

from emporos.core.clock import Clock
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Timeframe

DEFAULT_HOT_DAYS: Mapping[Timeframe, int | None] = {
    Timeframe.M1: 3,
    Timeframe.M5: 14,
    Timeframe.M15: 14,
    Timeframe.H1: 14,
    Timeframe.D1: None,  # never leaves the hot tier
}


class PlacementPolicy(Protocol):
    def hot_cutoff(self, timeframe: Timeframe) -> datetime | None:
        """Bars with `ts >= cutoff` belong in the hot tier, older ones in the cold tier.
        `None` means every bar of that timeframe is hot."""
        ...


class RetentionPlacement:
    def __init__(
        self, clock: Clock, hot_days: Mapping[Timeframe, int | None] | None = None
    ) -> None:
        days = dict(DEFAULT_HOT_DAYS if hot_days is None else hot_days)
        if any(d is not None and d < 1 for d in days.values()):
            raise ConfigurationError("hot retention must be at least one day")
        self._clock = clock
        self._days = days

    def hot_cutoff(self, timeframe: Timeframe) -> datetime | None:
        days = self._days.get(timeframe)
        return None if days is None else self._clock.now() - timedelta(days=days)
