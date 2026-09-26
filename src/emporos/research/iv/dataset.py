"""The daily IV and implied-move dataset (EM-244, driver-atlas-plan §7a).

One row per (underlying, day, expiry) for the nearest expiries of each kind: `monthly` (an expiry
that has its own future in the archive: index monthlies and all stock options) and `weekly` (index
weekly options, which have none). Every number comes from that day's own settle prices: the
`ChainSliceBuilder` refuses a row of another day. `IvStore` keeps a Parquet file per day."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from emporos.research.fo_archive_rows import IndexContractRow
from emporos.research.iv.metrics import SliceMetricsCalculator
from emporos.research.iv.rates import RateSource
from emporos.research.iv.slices import ChainSliceBuilder

__all__ = ["IvBuilder", "IvRow", "IvStore"]

NEAREST = 2  # expiries of each kind kept per underlying and day


@dataclass(frozen=True)
class IvRow:
    day: date
    symbol: str
    expiry: date
    kind: str  # "monthly" or "weekly"
    rank: int  # 1 = the nearest expiry of its kind
    days_to_expiry: int
    forward: float
    forward_source: str  # "future" or "parity"
    rate: float
    atm_strike: float
    atm_iv: float | None
    call25_iv: float | None
    put25_iv: float | None
    skew25: float | None
    straddle: float | None
    implied_move: float | None


_SCHEMA = pa.schema(
    [
        ("day", pa.date32()), ("symbol", pa.string()), ("expiry", pa.date32()),
        ("kind", pa.string()), ("rank", pa.int8()), ("days_to_expiry", pa.int32()),
        ("forward", pa.float64()), ("forward_source", pa.string()), ("rate", pa.float64()),
        ("atm_strike", pa.float64()), ("atm_iv", pa.float64()), ("call25_iv", pa.float64()),
        ("put25_iv", pa.float64()), ("skew25", pa.float64()), ("straddle", pa.float64()),
        ("implied_move", pa.float64()),
    ]
)  # fmt: skip


class IvBuilder:
    def __init__(self, rates: RateSource, min_contracts: int = 1, nearest: int = NEAREST) -> None:
        self._rates, self._nearest = rates, nearest
        self._slices = ChainSliceBuilder(min_contracts)
        self._metrics = SliceMetricsCalculator()

    def build_day(self, day: date, rows: Sequence[IndexContractRow]) -> list[IvRow]:
        rate = self._rates.rate(day)
        by_symbol: dict[str, list[IndexContractRow]] = defaultdict(list)
        for row in rows:
            by_symbol[row.symbol].append(row)
        out: list[IvRow] = []
        for symbol in sorted(by_symbol):
            ranks: dict[str, int] = defaultdict(int)
            for chain in self._slices.build(day, symbol, by_symbol[symbol], rate):
                kind = "monthly" if chain.has_future else "weekly"
                if ranks[kind] >= self._nearest:
                    continue
                metrics = self._metrics.compute(chain, rate)
                if metrics is None:
                    continue
                ranks[kind] += 1
                out.append(
                    IvRow(
                        day,
                        symbol,
                        chain.expiry,
                        kind,
                        ranks[kind],
                        chain.days_to_expiry,
                        chain.forward,
                        chain.forward_source,
                        rate,
                        metrics.atm_strike,
                        metrics.atm_iv,
                        metrics.call25_iv,
                        metrics.put25_iv,
                        metrics.skew25,
                        metrics.straddle,
                        metrics.implied_move,
                    )  # fmt: skip
                )
        return out


class IvStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, day: date) -> Path:
        return self._root / f"{day.year}" / f"{day.isoformat()}.parquet"

    def has(self, day: date) -> bool:
        return self.path(day).exists()

    def write(self, day: date, rows: Iterable[IvRow]) -> int:
        items = [r for r in rows]
        if any(r.day != day for r in items):
            raise ValueError("a row carries a different day than the file it is written to")
        columns = {f.name: [getattr(r, f.name) for r in items] for f in _SCHEMA}
        target = self.path(day)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(".parquet.tmp")
        pq.write_table(pa.Table.from_pydict(columns, schema=_SCHEMA), staging, compression="zstd")
        staging.replace(target)
        return len(items)

    def read(self, day: date) -> list[IvRow]:
        table = pq.read_table(self.path(day), schema=_SCHEMA)
        return [IvRow(**record) for record in table.to_pylist()]

    def days(self) -> list[date]:
        return sorted(date.fromisoformat(p.stem) for p in self._root.glob("*/*.parquet"))
