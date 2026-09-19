"""`CandleRepository` — the ONLY path to candle data (plan.md §6, Decision 5).

It unions the hot Mongo tier with the cold S3 Parquet archive behind one API.
Whether a range lives entirely in Mongo, entirely in S3, or spans both, the
returned bar series is identical — so moving the retention boundary (or the
whole Phase 19 hosting decision) is configuration, never a rewrite. Where both
tiers hold the same bar, the hot copy wins (it is the more recently written).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.candle_cold import ColdCandleArchive


class HotCandleStore(Protocol):
    async def upsert(self, candles: Sequence[Candle]) -> None: ...

    async def read(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...


class CandleReader(Protocol):
    """What strategies and backtests may depend on."""

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...


class CandleWriter(Protocol):
    """What the market-data pipeline and backfill may depend on."""

    async def upsert(self, candles: Sequence[Candle]) -> None: ...


class CandleRepository:
    def __init__(self, hot: HotCandleStore, cold: ColdCandleArchive) -> None:
        self._hot = hot
        self._cold = cold

    async def upsert(self, candles: Sequence[Candle]) -> None:
        await self._hot.upsert(candles)

    async def get_range(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Bars with `start <= ts < end`, oldest first, one per timestamp."""
        if start >= end:
            return []
        merged = {c.ts: c for c in await self._cold.read(instrument_id, timeframe, start, end)}
        merged |= {c.ts: c for c in await self._hot.read(instrument_id, timeframe, start, end)}
        return [merged[ts] for ts in sorted(merged)]
