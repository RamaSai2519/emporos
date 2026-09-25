"""Single-stock F&O from the same NSE bhavcopy archive, as its OWN dataset (EM-241, EM-240).

The index dataset (`fo_bhavcopy`, EM-225) keeps only index contracts and is not touched. This one
keeps stock futures (`FUTSTK`, UDiFF `STF`) and stock options (`OPTSTK`, `STO`), parsed by the same
row and file code, and thins them before they are stored, because a day holds about 28,000 stock
option rows:

* options: only expiries that are also a future's expiry that day (the monthlies), and only strikes
  within +-15% of that day's underlying level. The level is the file's own `UndrlygPric` (UDiFF),
  else the nearest future's settlement price (legacy files carry no underlying);
* futures: all rows (they give the level, the lot size inference and the basis);
* stored fields are the row's usual ones: close, settle, open interest, contracts, lot size (UDiFF
  only: a legacy file has none, and `StockLotBook` resolves it).

The dataset is versioned by its directory name (`DATASET`): a change in what is kept is a new
directory, never an edit in place."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar

from emporos.research.fo_archive_rows import (
    ArchiveFormat,
    ArchiveParser,
    IndexContractRow,
    InstrumentKind,
    LegacyParser,
    UdiffParser,
    csv_text_from_zip,
)

__all__ = [
    "DATASET",
    "STRIKE_BAND",
    "StockArchiveReader",
    "StockLegacyParser",
    "StockRowFilter",
    "StockUdiffParser",
]

DATASET = "fo_stock_v1"
STRIKE_BAND = Decimal("0.15")


class StockUdiffParser(UdiffParser):
    """UDiFF stock futures `STF` and stock options `STO`."""

    _KINDS: ClassVar[Mapping[str, InstrumentKind]] = {
        "STF": InstrumentKind.FUTURE,
        "STO": InstrumentKind.OPTION,
    }


class StockLegacyParser(LegacyParser):
    """Legacy `FUTSTK` and `OPTSTK` rows."""

    _KINDS: ClassVar[Mapping[str, InstrumentKind]] = {
        "FUTSTK": InstrumentKind.FUTURE,
        "OPTSTK": InstrumentKind.OPTION,
    }


@dataclass(frozen=True)
class StockRowFilter:
    """Thins one day's stock rows: monthly expiries, strikes near the money."""

    band: Decimal = STRIKE_BAND

    def __post_init__(self) -> None:
        if not 0 < self.band < 1:
            raise ValueError("the strike band is a fraction between 0 and 1")

    def apply(self, rows: Sequence[IndexContractRow]) -> list[IndexContractRow]:
        by_symbol: dict[str, list[IndexContractRow]] = defaultdict(list)
        for row in rows:
            by_symbol[row.symbol].append(row)
        kept: list[IndexContractRow] = []
        for symbol_rows in by_symbol.values():
            kept.extend(self._one(symbol_rows))
        return kept

    def _one(self, rows: Sequence[IndexContractRow]) -> list[IndexContractRow]:
        futures = [r for r in rows if r.kind is InstrumentKind.FUTURE]
        monthlies = {r.expiry for r in futures}
        level = self.level(rows)
        kept = list(futures)
        if level is None:
            return kept  # no level, no band: only the futures are kept
        low, high = level * (1 - self.band), level * (1 + self.band)
        for row in rows:
            if row.kind is not InstrumentKind.OPTION or row.strike is None:
                continue
            if monthlies and row.expiry not in monthlies:
                continue
            if low <= row.strike <= high:
                kept.append(row)
        return kept

    @staticmethod
    def level(rows: Sequence[IndexContractRow]) -> Decimal | None:
        """The underlying's level that day: the file's own, else the nearest future's settle."""
        carried = [r.underlying for r in rows if r.underlying is not None and r.underlying > 0]
        if carried:
            return statistics.median(carried)
        futures = sorted(
            (r for r in rows if r.kind is InstrumentKind.FUTURE and r.settle > 0),
            key=lambda r: r.expiry,
        )
        return futures[0].settle if futures else None


_PARSERS: Mapping[ArchiveFormat, Callable[[], ArchiveParser]] = {
    ArchiveFormat.LEGACY: StockLegacyParser,
    ArchiveFormat.UDIFF: StockUdiffParser,
}


class StockArchiveReader:
    """The fetcher's `reader`: a downloaded zip in, the thinned stock rows out."""

    def __init__(self, row_filter: StockRowFilter | None = None) -> None:
        self._filter = row_filter or StockRowFilter()

    def __call__(self, day: date, payload: bytes, fmt: ArchiveFormat) -> list[IndexContractRow]:
        parser = _PARSERS[fmt]()
        return self._filter.apply(parser.parse(day, csv_text_from_zip(payload)))
