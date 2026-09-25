"""Option contracts as the public scrip master lists them (EM-246).

`OptionContract` is what a recorded row is about; `ContractBook` is the day's list of them, asked
by underlying, expiry and strike. The master's strikes are in paise (x100) and its expiries are
`26SEP2024`; both are converted once, here. A row that does not parse is skipped, never guessed."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["ContractBook", "OptionContract"]

OPTION_KINDS = frozenset({"OPTIDX", "OPTSTK"})
PAISE = Decimal(100)


@dataclass(frozen=True)
class OptionContract:
    instrument_id: str  # "NFO:<token>"
    underlying: str  # the master's `name`: NIFTY, BANKNIFTY, RELIANCE
    expiry: date
    strike: Decimal  # rupees
    right: str  # "CE" or "PE"
    lot_size: int

    @classmethod
    def from_master_row(cls, row: Mapping[str, Any]) -> OptionContract | None:
        try:
            if row.get("exch_seg") != "NFO" or row.get("instrumenttype") not in OPTION_KINDS:
                return None
            symbol = str(row["symbol"])
            right = symbol[-2:]
            if right not in ("CE", "PE"):
                return None
            expiry = datetime.strptime(str(row["expiry"]), "%d%b%Y").date()
            strike = Decimal(str(row["strike"])) / PAISE
            return cls(
                f"NFO:{row['token']}", str(row["name"]), expiry, strike, right,
                int(str(row["lotsize"])),
            )  # fmt: skip
        except (KeyError, ValueError, InvalidOperation):
            return None


class ContractBook:
    def __init__(self, contracts: Iterable[OptionContract]) -> None:
        self._by_key: dict[tuple[str, date, Decimal, str], OptionContract] = {}
        self._expiries: dict[str, set[date]] = defaultdict(set)
        self._strikes: dict[tuple[str, date], set[Decimal]] = defaultdict(set)
        for c in contracts:
            self._by_key[(c.underlying, c.expiry, c.strike, c.right)] = c
            self._expiries[c.underlying].add(c.expiry)
            self._strikes[(c.underlying, c.expiry)].add(c.strike)

    @classmethod
    def from_master_rows(cls, rows: Iterable[Mapping[str, Any]]) -> ContractBook:
        return cls(c for row in rows if (c := OptionContract.from_master_row(row)) is not None)

    def __len__(self) -> int:
        return len(self._by_key)

    def expiries(self, underlying: str, on_or_after: date) -> list[date]:
        return sorted(e for e in self._expiries.get(underlying, ()) if e >= on_or_after)

    def strikes(self, underlying: str, expiry: date) -> list[Decimal]:
        return sorted(self._strikes.get((underlying, expiry), ()))

    def get(
        self, underlying: str, expiry: date, strike: Decimal, right: str
    ) -> OptionContract | None:
        return self._by_key.get((underlying, expiry, strike, right))
