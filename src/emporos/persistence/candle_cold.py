"""The cold tier: Parquet candle archives in object storage, one file per month.

Layout: `candles/{timeframe}/{instrument_id}/{YYYY-MM}.parquet` (UTC months).
Reading returns the same `Candle` values the hot tier does, so callers cannot
tell which tier served a bar. Writing a month merges with what is already
archived, so archiving is idempotent.
"""

from __future__ import annotations

import io
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.persistence.object_store import ObjectNotFoundError, ObjectStore

_PRICE = pa.decimal128(18, 6)
_SCHEMA = pa.schema(
    [
        ("instrument_id", pa.string()),
        ("timeframe", pa.string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("open", _PRICE),
        ("high", _PRICE),
        ("low", _PRICE),
        ("close", _PRICE),
        ("volume", pa.int64()),
        ("partial", pa.bool_()),
    ]
)


class ColdCandleArchive(Protocol):
    async def read(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...

    async def archive(self, candles: Iterable[Candle]) -> None: ...


class DisabledColdArchive:
    """The cold tier when no bucket is configured: it holds nothing and refuses to be written to.
    Reading it is fine (a range older than the hot retention is simply empty); silently dropping
    bars that belong in it would not be, so `archive` raises."""

    async def read(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        return []

    async def archive(self, candles: Iterable[Candle]) -> None:
        raise ConfigurationError(
            "S3_BUCKET is not set: bars older than their hot retention cannot be stored"
        )


class ParquetCandleCodec:
    def encode(self, candles: Sequence[Candle]) -> bytes:
        columns = {
            "instrument_id": [c.instrument_id for c in candles],
            "timeframe": [c.timeframe.value for c in candles],
            "ts": [c.ts for c in candles],
            "open": [c.open.amount for c in candles],
            "high": [c.high.amount for c in candles],
            "low": [c.low.amount for c in candles],
            "close": [c.close.amount for c in candles],
            "volume": [c.volume for c in candles],
            "partial": [c.partial for c in candles],
        }
        buffer = io.BytesIO()
        pq.write_table(pa.table(columns, schema=_SCHEMA), buffer)
        return buffer.getvalue()

    def decode(self, data: bytes) -> list[Candle]:
        rows: list[dict[str, Any]] = pq.read_table(io.BytesIO(data)).to_pylist()
        return [
            Candle(
                instrument_id=str(row["instrument_id"]),
                timeframe=Timeframe(str(row["timeframe"])),
                ts=row["ts"].astimezone(UTC),
                open=Money(row["open"]),
                high=Money(row["high"]),
                low=Money(row["low"]),
                close=Money(row["close"]),
                volume=int(row["volume"]),
                partial=bool(row["partial"]),
            )
            for row in rows
        ]


class ParquetCandleArchive:
    def __init__(self, store: ObjectStore, codec: ParquetCandleCodec | None = None) -> None:
        self._store = store
        self._codec = codec or ParquetCandleCodec()

    async def read(
        self, instrument_id: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        found: list[Candle] = []
        for month in self._months(start, end):
            found.extend(await self._read_month(instrument_id, timeframe, month))
        return sorted(
            (c for c in found if start <= c.ts < end),
            key=lambda c: c.ts,
        )

    async def archive(self, candles: Iterable[Candle]) -> None:
        """Merge `candles` into their monthly partitions; re-archiving the same bars is a no-op."""
        grouped: dict[tuple[str, Timeframe, str], list[Candle]] = defaultdict(list)
        for candle in candles:
            grouped[(candle.instrument_id, candle.timeframe, self._month_of(candle.ts))].append(
                candle
            )
        for (instrument_id, timeframe, month), incoming in grouped.items():
            existing = await self._read_month(instrument_id, timeframe, month)
            merged = {c.ts: c for c in existing} | {c.ts: c for c in incoming}
            ordered = [merged[ts] for ts in sorted(merged)]
            await self._store.put(
                self._key(instrument_id, timeframe, month), self._codec.encode(ordered)
            )

    async def _read_month(
        self, instrument_id: str, timeframe: Timeframe, month: str
    ) -> list[Candle]:
        try:
            data = await self._store.get(self._key(instrument_id, timeframe, month))
        except ObjectNotFoundError:
            return []
        return self._codec.decode(data)

    @staticmethod
    def _key(instrument_id: str, timeframe: Timeframe, month: str) -> str:
        return f"candles/{timeframe.value}/{instrument_id}/{month}.parquet"

    @staticmethod
    def _month_of(ts: datetime) -> str:
        return f"{ts.year:04d}-{ts.month:02d}"

    def _months(self, start: datetime, end: datetime) -> list[str]:
        months: list[str] = []
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            months.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return months
