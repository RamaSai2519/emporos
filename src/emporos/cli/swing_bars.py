"""Derived daily bars from local files, through the vault (EM-221, EM-228, EM-229)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta

from emporos.backtest.vault import VaultedCandleReader
from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe

__all__ = ["VaultedDailyBars"]


class VaultedDailyBars:
    """Derived daily bars from local files, through the vault: the sealed days cannot be read."""

    def __init__(self, reader: VaultedCandleReader) -> None:
        self._reader = reader

    def bars(self, instrument_id: str, first: date, last: date) -> list[Candle]:
        start = datetime.combine(first, time(0, 0), tzinfo=IST)
        end = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=IST)
        return asyncio.run(self._reader.get_range(instrument_id, Timeframe.D1, start, end))
