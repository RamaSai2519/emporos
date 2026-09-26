"""One underlying's option chain on one day, per expiry, from that day's settle prices (EM-244).

Only rows of the day itself are read: `ChainSliceBuilder.build` refuses a row dated otherwise, so no
settle price from after the day can enter a slice. The forward is the future's settle for the
expiry when the archive has that future (monthlies), else the put-call-parity forward at the strikes
nearest the money (weeklies have no future of their own). A contract that did not trade keeps a
carried close, so quotes need `min_contracts` lots traded."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from emporos.research.fo_archive_rows import IndexContractRow, InstrumentKind
from emporos.research.iv.black76 import Right

__all__ = ["ChainSlice", "ChainSliceBuilder", "Quote"]

DAYS_PER_YEAR = 365.0
PARITY_STRIKES = 3  # strikes nearest the money used for the parity forward


@dataclass(frozen=True)
class Quote:
    strike: float
    right: Right
    settle: float
    contracts: int


@dataclass(frozen=True)
class ChainSlice:
    symbol: str
    day: date
    expiry: date
    forward: float
    forward_source: str  # "future" or "parity"
    has_future: bool
    quotes: tuple[Quote, ...]

    @property
    def days_to_expiry(self) -> int:
        return (self.expiry - self.day).days

    @property
    def years(self) -> float:
        return self.days_to_expiry / DAYS_PER_YEAR


class ChainSliceBuilder:
    def __init__(self, min_contracts: int = 1) -> None:
        self._min_contracts = min_contracts

    def build(
        self, day: date, symbol: str, rows: Sequence[IndexContractRow], rate: float
    ) -> list[ChainSlice]:
        """The slices (one per expiry after `day` with a usable forward), soonest first."""
        futures: dict[date, float] = {}
        options: dict[date, list[Quote]] = defaultdict(list)
        for row in rows:
            if row.day != day:
                raise ValueError(f"a row dated {row.day} is not from {day}: no later prices")
            if row.symbol != symbol or row.expiry <= day:
                continue
            if row.kind is InstrumentKind.FUTURE:
                if row.contracts >= self._min_contracts and row.settle > 0:
                    futures[row.expiry] = float(row.settle)
            elif (
                row.right is not None and row.strike is not None
                and row.contracts >= self._min_contracts and row.settle > 0
            ):  # fmt: skip
                right = Right.CALL if row.right.value == "CE" else Right.PUT
                options[row.expiry].append(
                    Quote(float(row.strike), right, float(row.settle), row.contracts)
                )
        slices: list[ChainSlice] = []
        for expiry in sorted(options):
            quotes = tuple(sorted(options[expiry], key=lambda q: (q.strike, q.right.value)))
            years = (expiry - day).days / DAYS_PER_YEAR
            if expiry in futures:
                forward, source = futures[expiry], "future"
            else:
                parity = self._parity_forward(quotes, years, rate)
                if parity is None:
                    continue
                forward, source = parity, "parity"
            slices.append(
                ChainSlice(symbol, day, expiry, forward, source, expiry in futures, quotes)
            )
        return slices

    @staticmethod
    def _parity_forward(quotes: Sequence[Quote], years: float, rate: float) -> float | None:
        """`F = K + e^(rT) (C - P)` at the strikes where call and put prices are closest."""
        calls = {q.strike: q.settle for q in quotes if q.right is Right.CALL}
        puts = {q.strike: q.settle for q in quotes if q.right is Right.PUT}
        both = sorted(set(calls) & set(puts), key=lambda k: abs(calls[k] - puts[k]))
        if not both:
            return None
        chosen = both[:PARITY_STRIKES]
        growth = math.exp(rate * years)
        return sum(k + growth * (calls[k] - puts[k]) for k in chosen) / len(chosen)
