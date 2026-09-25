"""Industry -> sector index, for the context builder's `SectorMap` (EM-239).

Each D1 constituent file (`nifty100.csv`, `niftymidcap150.csv`) names a symbol's NSE industry. A
committed table (`config/universe/track-l/sector_index.csv`) maps an industry to the NIFTY sector
index that tracks it, where one does. An industry with no row, and a symbol in no constituent file
(the F&O stocks outside D1), have NO sector: missing stays missing, it is never guessed.

The industry is the one in the constituent files as fetched (today's classification); it is used
for every day of the window, so a name that changed industry is read with the current one. That is
stated in the prompt inputs' notes, not corrected here."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from emporos.research.market_context.builder import SectorMap

__all__ = ["DEFAULT_SECTOR_TABLE", "SectorMapLoader"]

DEFAULT_SECTOR_TABLE = Path("config/universe/track-l/sector_index.csv")


class SectorMapLoader:
    def __init__(self, table: Path = DEFAULT_SECTOR_TABLE) -> None:
        with table.open(encoding="utf-8", newline="") as handle:
            self._index = {
                r["industry"]: (r["index_id"], r["index_name"]) for r in csv.DictReader(handle)
            }

    def load(self, constituent_files: Iterable[Path]) -> SectorMap:
        by_symbol: dict[str, tuple[str, str]] = {}
        for path in constituent_files:
            with path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    found = self._index.get(row["Industry"].strip())
                    if found is not None:
                        by_symbol.setdefault(row["Symbol"].strip(), found)
        return SectorMap(by_symbol)
