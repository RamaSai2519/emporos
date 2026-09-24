"""Results filings as public events, and the append-only ledger that keeps them (EM-191 D5).

A result "became public" when the exchange disseminated the filing, so every event here carries the
exchange's own timestamp (`an_dt`, IST) and never a quarter-end or the day the price moved. The
reaction day is decided later, from `public_at`, by whoever needs it; nothing here looks at prices.

* `ResultsFiling` is one exchange filing tagged "Financial Result Updates".
* `first_public_results` collapses a name's filings (standalone and consolidated copies, revisions)
  into one event per results season: the EARLIEST filing of a cluster is when the market could first
  know. A filing more than `GAP_DAYS` after the cluster's first starts a new one; quarters are about
  ninety days apart, so a corrected re-filing is inside a cluster and the next quarter is not.
* `FilingLedger` appends filings to a JSONL file, once each (a name and the exchange's sequence id),
  and records which names were collected over which window, so a run resumes where it stopped.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from emporos.core.clock import IST

__all__ = [
    "GAP_DAYS",
    "RESULTS_SUBJECT",
    "FilingLedger",
    "ResultsFiling",
    "first_public_results",
    "parse_filings",
]

RESULTS_SUBJECT = "Financial Result Updates"
GAP_DAYS = 20
_STAMP = "%d-%b-%Y %H:%M:%S"


@dataclass(frozen=True)
class ResultsFiling:
    symbol: str
    isin: str
    public_at: datetime  # timezone-aware, IST: when the exchange published it
    seq_id: str
    text: str
    attachment: str

    def __post_init__(self) -> None:
        if self.public_at.tzinfo is None:
            raise ValueError("a filing's public time must carry a timezone")
        if not self.symbol or not self.seq_id:
            raise ValueError("a filing needs a symbol and the exchange's sequence id")

    def as_record(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "isin": self.isin,
            "public_at": self.public_at.isoformat(),
            "seq_id": self.seq_id,
            "text": self.text,
            "attachment": self.attachment,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, str]) -> ResultsFiling:
        return cls(
            record["symbol"],
            record["isin"],
            datetime.fromisoformat(record["public_at"]),
            record["seq_id"],
            record["text"],
            record["attachment"],
        )


def parse_filings(payload: object, symbol: str) -> list[ResultsFiling]:
    """The results filings in one exchange response for `symbol`. Rows tagged with any other
    subject are ignored; a malformed row is refused rather than guessed at."""
    if not isinstance(payload, list):
        raise ValueError(
            f"{symbol}: expected a list of announcements, got {type(payload).__name__}"
        )
    out: list[ResultsFiling] = []
    for row in payload:
        if not isinstance(row, dict) or row.get("desc") != RESULTS_SUBJECT:
            continue
        out.append(_filing(row, symbol))
    return sorted(out, key=lambda f: f.public_at)


def _filing(row: Mapping[str, Any], symbol: str) -> ResultsFiling:
    try:
        public_at = datetime.strptime(str(row["an_dt"]), _STAMP).replace(tzinfo=IST)
        seq_id = str(row["seq_id"])
    except (KeyError, ValueError) as error:
        raise ValueError(
            f"{symbol}: a results filing without a usable time or id: {row}"
        ) from error
    return ResultsFiling(
        str(row.get("symbol") or symbol),
        str(row.get("sm_isin") or ""),
        public_at,
        seq_id,
        str(row.get("attchmntText") or "").strip(),
        str(row.get("attchmntFile") or ""),
    )


def first_public_results(filings: Iterable[ResultsFiling]) -> list[ResultsFiling]:
    """One filing per name per results season: the earliest of each cluster, oldest first."""
    by_symbol: dict[str, list[ResultsFiling]] = {}
    for filing in filings:
        by_symbol.setdefault(filing.symbol, []).append(filing)
    firsts: list[ResultsFiling] = []
    for symbol_filings in by_symbol.values():
        cluster_start: datetime | None = None
        for filing in sorted(symbol_filings, key=lambda f: f.public_at):
            if cluster_start is None or filing.public_at - cluster_start > timedelta(days=GAP_DAYS):
                cluster_start = filing.public_at
                firsts.append(filing)
    return sorted(firsts, key=lambda f: (f.public_at, f.symbol))


class FilingLedger:
    """Append-only: filings are only ever added, and each (symbol, seq id) once."""

    def __init__(self, filings: Path, manifest: Path) -> None:
        self._filings = filings
        self._manifest = manifest

    def load(self) -> list[ResultsFiling]:
        if not self._filings.exists():
            return []
        with self._filings.open(encoding="utf-8") as handle:
            return [ResultsFiling.from_record(json.loads(line)) for line in handle if line.strip()]

    def collected_symbols(self) -> set[str]:
        if not self._manifest.exists():
            return set()
        with self._manifest.open(encoding="utf-8") as handle:
            return {json.loads(line)["symbol"] for line in handle if line.strip()}

    def record(
        self, symbol: str, filings: Sequence[ResultsFiling], first: date, last: date, at: datetime
    ) -> int:
        """Add the filings not already held, note the name as collected; returns the new count."""
        held = {(f.symbol, f.seq_id) for f in self.load()}
        new = [f for f in filings if (f.symbol, f.seq_id) not in held]
        self._filings.parent.mkdir(parents=True, exist_ok=True)
        if new:
            with self._filings.open("a", encoding="utf-8") as handle:
                for filing in new:
                    handle.write(json.dumps(filing.as_record(), sort_keys=True) + "\n")
        entry = {
            "symbol": symbol,
            "first": first.isoformat(),
            "last": last.isoformat(),
            "filings": len(filings),
            "collected_at": at.isoformat(),
        }
        with self._manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return len(new)
