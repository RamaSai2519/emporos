"""One recorded L1 quote of an option contract, and where those go (EM-246).

Same guarantees as the D1 stock quotes (`sink.py`): append-only part files per IST day, a flush
writes a new file, nothing is rewritten. They live under their own root (`quotes-options/` on S3,
`data/quotes-options` locally), so the D1 files and their readers are untouched."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import pyarrow as pa

from emporos.core.clock import IST, Clock
from emporos.quotes.contract import OptionContract
from emporos.quotes.row import QuoteRow
from emporos.quotes.sink import PartWriter

__all__ = ["OptionQuoteRow", "OptionQuoteSink", "ParquetOptionSink"]

_PRICE = pa.decimal128(14, 2)
SCHEMA = pa.schema(
    [
        ("instrument_id", pa.string()),
        ("underlying", pa.string()),
        ("expiry", pa.date32()),
        ("strike", _PRICE),
        ("right", pa.string()),
        ("lot_size", pa.int32()),
        ("spot", _PRICE),  # the underlying's price the strike set was built from
        ("received_at", pa.timestamp("us", tz="UTC")),
        ("exchange_ts", pa.timestamp("us", tz="UTC")),
        ("ltp", _PRICE),
        ("bid", _PRICE),
        ("ask", _PRICE),
        ("bid_qty", pa.int64()),
        ("ask_qty", pa.int64()),
        ("volume", pa.int64()),
        ("open_interest", pa.int64()),
    ]
)
_PAISE = Decimal("0.01")


@dataclass(frozen=True)
class OptionQuoteRow:
    quote: QuoteRow
    contract: OptionContract
    spot: Decimal
    open_interest: int | None


class OptionQuoteSink(Protocol):
    def append(self, rows: Sequence[OptionQuoteRow]) -> None: ...

    def flush(self) -> int: ...


def _price(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(_PAISE)


def _table(rows: Sequence[OptionQuoteRow]) -> pa.Table:
    return pa.table(
        {
            "instrument_id": [r.contract.instrument_id for r in rows],
            "underlying": [r.contract.underlying for r in rows],
            "expiry": [r.contract.expiry for r in rows],
            "strike": [_price(r.contract.strike) for r in rows],
            "right": [r.contract.right for r in rows],
            "lot_size": [r.contract.lot_size for r in rows],
            "spot": [_price(r.spot) for r in rows],
            "received_at": [r.quote.received_at for r in rows],
            "exchange_ts": [r.quote.exchange_ts for r in rows],
            "ltp": [_price(r.quote.ltp) for r in rows],
            "bid": [_price(r.quote.bid) for r in rows],
            "ask": [_price(r.quote.ask) for r in rows],
            "bid_qty": [r.quote.bid_qty for r in rows],
            "ask_qty": [r.quote.ask_qty for r in rows],
            "volume": [r.quote.volume for r in rows],
            "open_interest": [r.open_interest for r in rows],
        },
        schema=SCHEMA,
    )


class ParquetOptionSink:
    def __init__(self, root: Path, clock: Clock) -> None:
        self._writer = PartWriter(root, clock)
        self._buffer: list[OptionQuoteRow] = []

    def append(self, rows: Sequence[OptionQuoteRow]) -> None:
        self._buffer.extend(rows)

    def flush(self) -> int:
        if not self._buffer:
            return 0
        by_day: dict[date, list[OptionQuoteRow]] = defaultdict(list)
        for row in self._buffer:
            by_day[row.quote.received_at.astimezone(IST).date()].append(row)
        for day, rows in sorted(by_day.items()):
            self._writer.write(day, _table(rows))
        written = len(self._buffer)
        self._buffer.clear()
        return written
