"""What the stock F&O dataset holds and what it lacks (EM-241): files, rows, days asked about that
have no file, and every change of a stock's lot size seen in the exchange's own numbers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pyarrow.compute as pc
import pyarrow.parquet as pq

from emporos.research.fo_archive_fetch import weekdays_descending
from emporos.research.fo_archive_store import FetchOutcome, FoDayStore, FoLedger

__all__ = ["LotChange", "StockArchiveReport", "StockArchiveReporter"]

_COLUMNS = ["symbol", "kind", "expiry", "lot_size", "source"]


@dataclass(frozen=True)
class LotChange:
    symbol: str
    first_seen: date  # the first stored day carrying the new lot size
    old: int
    new: int


@dataclass(frozen=True)
class StockArchiveReport:
    files: int
    rows: int
    first_day: date | None
    last_day: date | None
    holidays: tuple[date, ...]  # weekdays the exchange answered 404 for
    unfetched: tuple[date, ...]  # weekdays with neither a file nor a 404 on record
    lot_changes: tuple[LotChange, ...]
    symbols_without_exchange_lot: int  # names that only ever appear in legacy (lot-less) files


class StockArchiveReporter:
    def __init__(self, store: FoDayStore, ledger: FoLedger) -> None:
        self._store, self._ledger = store, ledger

    def build(self, first: date, last: date) -> StockArchiveReport:
        days = [d for d in self._store.days() if first <= d <= last]
        absent = {e.day for e in self._ledger.load() if e.outcome is FetchOutcome.ABSENT}
        stored = set(days)
        weekdays = list(weekdays_descending(first, last))
        holidays = tuple(sorted(d for d in weekdays if d in absent and d not in stored))
        unfetched = tuple(sorted(d for d in weekdays if d not in absent and d not in stored))
        rows = sum(pq.ParquetFile(self._store.path(d)).metadata.num_rows for d in days)
        changes, without = self._lot_changes(days)
        return StockArchiveReport(
            len(days), rows, days[0] if days else None, days[-1] if days else None, holidays,
            unfetched, changes, without,
        )  # fmt: skip

    def _lot_changes(self, days: list[date]) -> tuple[tuple[LotChange, ...], int]:
        """Per stock, the near-month future's exchange lot size day by day; a change is reported
        at the first day it shows."""
        latest: dict[str, int] = {}
        seen: set[str] = set()
        changes: list[LotChange] = []
        every: set[str] = set()
        for day in days:
            table = pq.read_table(self._store.path(day), columns=_COLUMNS)
            table = table.filter(pc.equal(table["kind"], "FUT"))
            rows = sorted((r for r in table.to_pylist()), key=lambda r: (r["symbol"], r["expiry"]))
            nearest: dict[str, int | None] = {}
            for r in rows:
                every.add(r["symbol"])
                nearest.setdefault(r["symbol"], r["lot_size"])
            for symbol, lot in nearest.items():
                if lot is None:
                    continue
                seen.add(symbol)
                before = latest.get(symbol)
                if before is not None and before != lot:
                    changes.append(LotChange(symbol, day, before, lot))
                latest[symbol] = lot
        return tuple(changes), len(every - seen)
