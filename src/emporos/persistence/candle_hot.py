"""The hot tier: the Mongo `candles` collection.

This module is the ONLY code that touches that collection — everything else goes
through `CandleRepository` (Decision 5), enforced by import-linter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pymongo import ASCENDING, ReplaceOne
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.candles import Candle, Timeframe
from emporos.persistence.collections import Collection
from emporos.persistence.money_codec import MoneyCodec


class CandleDocumentMapper:
    """Candle ⇄ Mongo document (prices as `Decimal128`)."""

    def __init__(self, codec: MoneyCodec | None = None) -> None:
        self._codec = codec or MoneyCodec()

    def key(self, candle: Candle) -> dict[str, Any]:
        return {
            "instrument_id": candle.instrument_id,
            "timeframe": candle.timeframe,
            "ts": candle.ts,
        }

    def to_document(self, candle: Candle) -> dict[str, Any]:
        encode = self._codec.encode
        return self.key(candle) | {
            "open": encode(candle.open),
            "high": encode(candle.high),
            "low": encode(candle.low),
            "close": encode(candle.close),
            "volume": candle.volume,
            "partial": candle.partial,
        }

    def from_document(self, document: Mapping[str, Any]) -> Candle:
        decode = self._codec.decode
        return Candle(
            instrument_id=document["instrument_id"],
            timeframe=Timeframe(document["timeframe"]),
            ts=document["ts"],
            open=decode(document["open"]),
            high=decode(document["high"]),
            low=decode(document["low"]),
            close=decode(document["close"]),
            volume=document["volume"],
            partial=document.get("partial", False),
        )


class MongoCandleStore:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        mapper: CandleDocumentMapper | None = None,
    ) -> None:
        self._collection = database[Collection.CANDLES]
        self._mapper = mapper or CandleDocumentMapper()

    async def upsert(self, candles: Sequence[Candle]) -> None:
        """Idempotent write keyed by the unique (instrument, timeframe, ts) index."""
        if not candles:
            return
        operations: list[ReplaceOne[Mapping[str, Any]]] = [
            ReplaceOne(self._mapper.key(c), self._mapper.to_document(c), upsert=True)
            for c in candles
        ]
        await self._collection.bulk_write(operations, ordered=False)

    async def read(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Bars with `start <= ts < end`, oldest first."""
        query = {
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "ts": {"$gte": start, "$lt": end},
        }
        cursor = self._collection.find(query, sort=[("ts", ASCENDING)])
        return [self._mapper.from_document(document) async for document in cursor]
