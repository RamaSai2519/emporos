"""The stock options worth recording quotes for (EM-246, plan §7a): the most liquid ones.

Liquidity is the lots traded across a stock's option contracts on the most recent session(s) of the
stock F&O dataset. Only names whose cash quote we can read (the D1 symbol table) qualify: the
recorder needs the underlying's price to centre its strikes. The list is fixed for a month."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from emporos.research.fo_archive_rows import IndexContractRow, InstrumentKind

__all__ = ["OptionUnderlying", "top_option_underlyings"]


class OptionDays(Protocol):
    def days(self) -> list[date]: ...

    def read(self, day: date, symbol: str | None = None) -> list[IndexContractRow]: ...


@dataclass(frozen=True)
class OptionUnderlying:
    symbol: str
    spot_id: str
    lots: int


def top_option_underlyings(
    store: OptionDays, spot_ids: Mapping[str, str], count: int = 20, sessions: int = 1
) -> tuple[tuple[date, ...], list[OptionUnderlying]]:
    """(the sessions used, the `count` most traded names, busiest first; ties by symbol)."""
    days = tuple(store.days()[-sessions:])
    lots: Counter[str] = Counter()
    for day in days:
        for row in store.read(day):
            if row.kind is InstrumentKind.OPTION and row.symbol in spot_ids:
                lots[row.symbol] += row.contracts
    ranked = sorted(lots.items(), key=lambda kv: (-kv[1], kv[0]))[:count]
    return days, [OptionUnderlying(s, spot_ids[s], n) for s, n in ranked]
