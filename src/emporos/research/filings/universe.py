"""Whose filings Track L collects: the D1 research names plus the F&O stock list (§12.2).

The D1 names are the manifest's `included` (never its held-out set: the instrument-sealed holdout
is not asked about, PROFIT_PLAN §4.3). The F&O stock list is the exchange's published market-lot
file (`fo_mktlots.csv`, committed with its fetch date): the underlyings of individual-security
derivatives, today's list (a name that joined or left the segment during 2024-26 is a known limit).
A held-out D1 name that is also in the F&O list stays out."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

__all__ = ["FilingUniverse", "fo_stock_symbols"]

_STOCKS_HEADING = "Derivatives on Individual Securities"


def fo_stock_symbols(text: str) -> list[str]:
    """The symbols under the file's 'Derivatives on Individual Securities' heading."""
    rows = list(csv.reader(io.StringIO(text)))
    symbols: list[str] = []
    in_stocks = False
    for row in rows:
        cells = [c.strip() for c in row]
        if not cells or not cells[0]:
            continue
        if cells[0] == _STOCKS_HEADING:
            in_stocks = True
            continue
        if in_stocks and len(cells) > 1 and cells[1]:
            symbols.append(cells[1])
    if not symbols:
        raise ValueError("the market-lot file lists no individual-security derivatives")
    return sorted(set(symbols))


@dataclass(frozen=True)
class FilingUniverse:
    d1: tuple[str, ...]  # D1 included symbols
    held_out: frozenset[str]  # D1 held-out symbols: never asked about
    fo_stocks: tuple[str, ...]

    @property
    def symbols(self) -> list[str]:
        return sorted((set(self.d1) | set(self.fo_stocks)) - self.held_out)

    @staticmethod
    def load(
        d1: dict[str, str],
        held_out_ids: frozenset[str],
        token_symbols: dict[str, str],
        fo_file: Path,
    ) -> FilingUniverse:
        """`d1`: symbol -> instrument id of the included names; `token_symbols`: id -> symbol for
        every D1 name (so the held-out ids can be named)."""
        held = frozenset(token_symbols[i] for i in held_out_ids if i in token_symbols)
        return FilingUniverse(
            tuple(sorted(d1)), held, tuple(fo_stock_symbols(fo_file.read_text(encoding="utf-8")))
        )
