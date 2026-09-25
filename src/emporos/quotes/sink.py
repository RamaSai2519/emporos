"""Where recorded quotes go: append-only Parquet part files, one directory per IST day.

`<root>/date=YYYY-MM-DD/part-<HHMMSSffffff>-<n>.parquet`. A flush writes a NEW file (to a temporary
name, then renamed), so a crash loses at most the rows still in memory and never damages a file
already written. Nothing is rewritten and nothing goes to Mongo."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from emporos.core.clock import IST, Clock
from emporos.quotes.row import QuoteRow

__all__ = ["ParquetQuoteSink", "PartWriter", "QuoteSink", "read_day"]

_PRICE = pa.decimal128(14, 2)
SCHEMA = pa.schema(
    [
        ("instrument_id", pa.string()),
        ("received_at", pa.timestamp("us", tz="UTC")),
        ("exchange_ts", pa.timestamp("us", tz="UTC")),
        ("ltp", _PRICE),
        ("bid", _PRICE),
        ("ask", _PRICE),
        ("bid_qty", pa.int64()),
        ("ask_qty", pa.int64()),
        ("volume", pa.int64()),
    ]
)
_PAISE = Decimal("0.01")


class QuoteSink(Protocol):
    def append(self, rows: Sequence[QuoteRow]) -> None: ...

    def flush(self) -> int:
        """Write what is buffered; the number of rows written."""
        ...


def _price(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(_PAISE)


def _table(rows: Sequence[QuoteRow]) -> pa.Table:
    return pa.table(
        {
            "instrument_id": [r.instrument_id for r in rows],
            "received_at": [r.received_at for r in rows],
            "exchange_ts": [r.exchange_ts for r in rows],
            "ltp": [_price(r.ltp) for r in rows],
            "bid": [_price(r.bid) for r in rows],
            "ask": [_price(r.ask) for r in rows],
            "bid_qty": [r.bid_qty for r in rows],
            "ask_qty": [r.ask_qty for r in rows],
            "volume": [r.volume for r in rows],
        },
        schema=SCHEMA,
    )


class PartWriter:
    """Writes one table as a NEW part file under its IST day's directory (temporary name, then
    renamed): shared by every quote sink, so a file is never rewritten."""

    def __init__(self, root: Path, clock: Clock) -> None:
        self._root, self._clock, self._files = root, clock, 0

    def write(self, day: date, table: pa.Table) -> None:
        directory = self._root / f"date={day.isoformat()}"
        directory.mkdir(parents=True, exist_ok=True)
        self._files += 1
        stamp = self._clock.now().strftime("%H%M%S%f")
        final = directory / f"part-{stamp}-{self._files:05d}.parquet"
        temporary = final.with_suffix(".tmp")
        pq.write_table(table, temporary, compression="zstd")
        os.replace(temporary, final)


class ParquetQuoteSink:
    def __init__(self, root: Path, clock: Clock) -> None:
        self._writer = PartWriter(root, clock)
        self._buffer: list[QuoteRow] = []

    def append(self, rows: Sequence[QuoteRow]) -> None:
        self._buffer.extend(rows)

    def flush(self) -> int:
        if not self._buffer:
            return 0
        by_day: dict[date, list[QuoteRow]] = defaultdict(list)
        for row in self._buffer:
            by_day[row.received_at.astimezone(IST).date()].append(row)
        written = 0
        for day, rows in sorted(by_day.items()):
            self._write(day, rows)
            written += len(rows)
        self._buffer.clear()
        return written

    def _write(self, day: date, rows: list[QuoteRow]) -> None:
        self._writer.write(day, _table(rows))


def read_day(root: Path, day: date) -> pa.Table:
    """Every row recorded for one IST day, in file order (empty table if none)."""
    directory = root / f"date={day.isoformat()}"
    files = sorted(directory.glob("part-*.parquet")) if directory.exists() else []
    if not files:
        return SCHEMA.empty_table()
    return pa.concat_tables([pq.ParquetFile(f).read() for f in files])
