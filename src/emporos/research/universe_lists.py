"""The D1 constituent lists NSE Indices publishes, as typed rows (EM-191, EDGE_SEARCH_PLAN.md D1).

One job: read a published index-constituent CSV (`Company Name,Industry,Symbol,Series,ISIN Code`)
and hand back the cash-equity names in it, with the trading symbol the broker's master uses
(`<SYMBOL>-EQ`). Nothing here reaches a network: the files are committed under `config/universe/`
with their provenance, so a run is reproducible and the download happened once.

The lists are the CURRENT constituents; see `config/universe/d1/SOURCE.yaml` for the survivorship
caveat."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Constituent", "ConstituentList", "combined_symbols"]

_HEADER = ("Company Name", "Industry", "Symbol", "Series", "ISIN Code")


@dataclass(frozen=True)
class Constituent:
    company: str
    industry: str
    symbol: str
    isin: str

    @property
    def trading_symbol(self) -> str:
        """The broker master's symbol for the cash-equity series."""
        return f"{self.symbol}-EQ"


class ConstituentList:
    def __init__(self, index: str, rows: Sequence[Constituent]) -> None:
        if not rows:
            raise ValueError(f"{index}: a constituent list cannot be empty")
        symbols = [r.symbol for r in rows]
        if len(set(symbols)) != len(symbols):
            raise ValueError(f"{index}: a symbol appears twice")
        self._index = index
        self._rows = tuple(rows)

    @classmethod
    def parse(cls, index: str, text: str) -> ConstituentList:
        reader = csv.reader(io.StringIO(text.lstrip("﻿")))
        header = tuple(cell.strip() for cell in next(reader, ()))
        if header != _HEADER:
            raise ValueError(f"{index}: expected the header {_HEADER}, got {header}")
        rows: list[Constituent] = []
        for line in reader:
            if not line:
                continue
            company, industry, symbol, series, isin = (cell.strip() for cell in line)
            if series != "EQ":
                raise ValueError(f"{index}: {symbol} is series {series}, not EQ")
            rows.append(Constituent(company, industry, symbol, isin))
        return cls(index, rows)

    @classmethod
    def load(cls, index: str, path: Path) -> ConstituentList:
        return cls.parse(index, path.read_text(encoding="utf-8"))

    @property
    def index(self) -> str:
        return self._index

    @property
    def rows(self) -> tuple[Constituent, ...]:
        return self._rows

    def __len__(self) -> int:
        return len(self._rows)


def combined_symbols(lists: Iterable[ConstituentList]) -> list[str]:
    """Trading symbols across lists, in list order, each once (an index pair can overlap)."""
    seen: set[str] = set()
    out: list[str] = []
    for constituents in lists:
        for row in constituents.rows:
            if row.trading_symbol not in seen:
                seen.add(row.trading_symbol)
                out.append(row.trading_symbol)
    return out
