"""Single-stock option chains from the stored stock F&O dataset (EM-241), for the event trader.

`StockChains.snapshot(symbol, day)` builds the end-of-day `ChainSnapshot` of one stock from that
day's stored rows. Two things a stock file does not always carry are worked out here, and only ever
to a value the exchange itself has published for that stock:

* the underlying's level: the file's own (UDiFF), else the nearest future's settlement price;
* the lot size: exact in UDiFF files. A legacy file has none, so it is inferred from the futures'
  turnover (turnover / (contracts x close)) and accepted ONLY if it lands within 5% of a lot size
  the exchange published for that stock in some other file (`StockLotBook`); a stock lot such as
  1,500 is far too large for the index code's snap-to-5, and an unmatched estimate is refused (no
  lot size, so no chain that day) rather than guessed."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise

import pyarrow.parquet as pq

from emporos.options.chain import ChainSnapshot, ExpiryChain, OptionQuote, OptionRight
from emporos.research.fo_archive_rows import IndexContractRow, InstrumentKind
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.fo_stock_archive import StockRowFilter

__all__ = ["StockChains", "StockLotBook"]

MATCH_TOLERANCE = Decimal("0.05")
MIN_LOTS_TRADED = 10  # fewer lots and the turnover's rounding swamps the price
TICK = Decimal("0.05")


class StockLotBook:
    """The lot sizes the exchange has published for each stock, and the resolution of a day's."""

    def __init__(self, published: Mapping[str, Iterable[int]]) -> None:
        self._published = {s: sorted(set(lots)) for s, lots in published.items()}

    @classmethod
    def from_store(cls, store: FoDayStore) -> StockLotBook:
        found: dict[str, set[int]] = defaultdict(set)
        for day in store.days():
            table = pq.read_table(store.path(day), columns=["symbol", "lot_size"])
            for symbol, lot in zip(
                table["symbol"].to_pylist(), table["lot_size"].to_pylist(), strict=True
            ):
                if lot is not None:
                    found[symbol].add(int(lot))
        return cls(found)

    def published(self, symbol: str) -> tuple[int, ...]:
        return tuple(self._published.get(symbol, ()))

    def resolve(self, symbol: str, rows: Sequence[IndexContractRow]) -> dict[date, int]:
        """The lot size of each expiry listed in `rows` (a day of one stock), where known."""
        exact = {r.expiry: r.lot_size for r in rows if r.lot_size is not None}
        lots: dict[date, int] = {e: int(v) for e, v in exact.items()}
        for expiry, estimate in self._estimates(rows).items():
            if expiry not in lots and (matched := self._match(symbol, estimate)) is not None:
                lots[expiry] = matched
        return lots

    @staticmethod
    def _estimates(rows: Sequence[IndexContractRow]) -> dict[date, Decimal]:
        per: dict[date, list[Decimal]] = defaultdict(list)
        for r in rows:
            if r.kind is InstrumentKind.FUTURE and r.contracts >= MIN_LOTS_TRADED and r.close > 0:
                per[r.expiry].append(r.turnover / (Decimal(r.contracts) * r.close))
        return {e: statistics.median(v) for e, v in per.items()}

    def _match(self, symbol: str, estimate: Decimal) -> int | None:
        close = [
            (abs(Decimal(lot) - estimate) / Decimal(lot), lot) for lot in self.published(symbol)
        ]
        close = [c for c in close if c[0] <= MATCH_TOLERANCE]
        return min(close)[1] if close else None


class StockChains:
    """Implements the event trader's `OptionChains`."""

    def __init__(self, store: FoDayStore, lots: StockLotBook) -> None:
        self._store, self._lots = store, lots

    def snapshot(self, symbol: str, day: date) -> ChainSnapshot | None:
        if not self._store.has(day):
            return None
        rows = self._store.read(day, symbol)
        options = [r for r in rows if r.kind is InstrumentKind.OPTION]
        level = StockRowFilter.level(rows)
        lots = self._lots.resolve(symbol, rows)
        if not options or level is None or not lots:
            return None
        by_expiry: dict[date, dict[tuple[Decimal, OptionRight], OptionQuote]] = defaultdict(dict)
        for r in options:
            assert r.strike is not None and r.right is not None
            by_expiry[r.expiry][(r.strike, r.right)] = OptionQuote(
                r.strike, r.right, r.close, r.settle, r.open_interest, r.contracts
            )
        nearest = min(lots)
        expiries = {
            e: ExpiryChain(e, q, lots.get(e, lots[nearest])) for e, q in sorted(by_expiry.items())
        }
        return ChainSnapshot(day, symbol, level, lots[nearest], self._step(options), TICK, expiries)

    @staticmethod
    def _step(options: Sequence[IndexContractRow]) -> Decimal:
        strikes = sorted({r.strike for r in options if r.strike is not None})
        gaps = [b - a for a, b in pairwise(strikes) if b > a]
        return min(gaps) if gaps else Decimal(1)
