"""What a source answered, kept verbatim, and the ledger that says where it came from (§8).

`RawFilingStore` writes each reply exactly as received
(`<root>/<source>/<symbol>/<first>_<last>.json`), atomically, so parsing rules can change without
asking the exchange again. `FetchLedger` is append-only JSONL, one line per window fetched: the
source URL, the fetch time, the count, the size and a hash. A window in the ledger is not fetched
again, so an interrupted run resumes."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

__all__ = [
    "DEFAULT_FILINGS_LEDGER",
    "DEFAULT_RAW_DIR",
    "FetchLedger",
    "FetchRecord",
    "RawFilingStore",
]

DEFAULT_RAW_DIR = Path.home() / ".cache" / "emporos" / "filings" / "raw"
DEFAULT_FILINGS_LEDGER = Path("docs/research/profit/filings-ledger.jsonl")


@dataclass(frozen=True)
class FetchRecord:
    source: str
    symbol: str
    first: date
    last: date
    url: str
    fetched_at: datetime
    count: int
    size: int
    sha256: str

    def as_document(self) -> dict[str, object]:
        return {
            "source": self.source, "symbol": self.symbol, "first": self.first.isoformat(),
            "last": self.last.isoformat(), "url": self.url,
            "fetched_at": self.fetched_at.isoformat(), "count": self.count, "size": self.size,
            "sha256": self.sha256,
        }  # fmt: skip


class RawFilingStore:
    def __init__(self, root: Path = DEFAULT_RAW_DIR) -> None:
        self._root = root

    def path(self, source: str, symbol: str, first: date, last: date) -> Path:
        safe = symbol.replace("&", "_and_").replace("/", "_")
        return self._root / source.lower() / safe / f"{first.isoformat()}_{last.isoformat()}.json"

    def write(self, source: str, symbol: str, first: date, last: date, body: bytes) -> str:
        """Store the reply; the sha256 of what was written."""
        target = self.path(source, symbol, first, last)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(body)
        os.replace(temporary, target)
        return hashlib.sha256(body).hexdigest()

    def read(self, source: str, symbol: str, first: date, last: date) -> bytes:
        return self.path(source, symbol, first, last).read_bytes()


class FetchLedger:
    def __init__(self, path: Path = DEFAULT_FILINGS_LEDGER) -> None:
        self._path = path

    def records(self) -> list[FetchRecord]:
        if not self._path.exists():
            return []
        out: list[FetchRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                out.append(
                    FetchRecord(
                        d["source"],
                        d["symbol"],
                        date.fromisoformat(d["first"]),
                        date.fromisoformat(d["last"]),
                        d["url"],
                        datetime.fromisoformat(d["fetched_at"]),
                        d["count"],
                        d["size"],
                        d["sha256"],
                    )  # fmt: skip
                )
        return out

    def done(self) -> set[tuple[str, str, date, date]]:
        return {(r.source, r.symbol, r.first, r.last) for r in self.records()}

    def record(self, record: FetchRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_document(), sort_keys=True) + "\n")
