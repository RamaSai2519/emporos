"""Local storage for the F&O archive: compact Parquet per trading day, and a ledger of every fetch
(EM-225). Nothing here touches Atlas: the data stays on the development machine.

`FoDayStore` writes `<root>/<YYYY>/<YYYY-MM-DD>.parquet` atomically (temp file, then rename), so an
interrupted run never leaves a half-written day. `FoLedger` is append-only JSONL: one line per date
asked about, recording the source URL, the fetch time, the size and a hash of the download, so every
file's provenance is on record (PROFIT_PLAN §8)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from emporos.options.chain import OptionRight
from emporos.research.fo_archive_rows import ArchiveFormat, IndexContractRow, InstrumentKind

__all__ = ["FetchOutcome", "FoDayStore", "FoLedger", "LedgerEntry"]

_PRICE = pa.decimal128(18, 6)
_MONEY = pa.decimal128(24, 2)
_SCHEMA = pa.schema(
    [
        ("day", pa.date32()), ("symbol", pa.string()), ("kind", pa.string()),
        ("expiry", pa.date32()), ("strike", _PRICE), ("right", pa.string()),
        ("open", _PRICE), ("high", _PRICE), ("low", _PRICE), ("close", _PRICE),
        ("settle", _PRICE), ("contracts", pa.int64()), ("turnover", _MONEY),
        ("open_interest", pa.int64()), ("change_in_oi", pa.int64()),
        ("underlying", _PRICE), ("lot_size", pa.int32()), ("source", pa.string()),
    ]
)  # fmt: skip


class FoDayStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, day: date) -> Path:
        return self._root / f"{day.year}" / f"{day.isoformat()}.parquet"

    def has(self, day: date) -> bool:
        return self.path(day).exists()

    def write(self, day: date, rows: Iterable[IndexContractRow]) -> int:
        materialised = sorted(
            rows, key=lambda r: (r.symbol, r.kind.value, r.expiry, r.strike or Decimal(0),
                                 r.right.value if r.right else "")
        )  # fmt: skip
        if any(r.day != day for r in materialised):
            raise ValueError("a row carries a different day than the file it is written to")
        table = pa.Table.from_pydict(
            {
                "day": [r.day for r in materialised],
                "symbol": [r.symbol for r in materialised],
                "kind": [r.kind.value for r in materialised],
                "expiry": [r.expiry for r in materialised],
                "strike": [r.strike for r in materialised],
                "right": [r.right.value if r.right else None for r in materialised],
                "open": [r.open for r in materialised],
                "high": [r.high for r in materialised],
                "low": [r.low for r in materialised],
                "close": [r.close for r in materialised],
                "settle": [r.settle for r in materialised],
                "contracts": [r.contracts for r in materialised],
                "turnover": [r.turnover for r in materialised],
                "open_interest": [r.open_interest for r in materialised],
                "change_in_oi": [r.change_in_oi for r in materialised],
                "underlying": [r.underlying for r in materialised],
                "lot_size": [r.lot_size for r in materialised],
                "source": [r.source.value for r in materialised],
            },
            schema=_SCHEMA,
        )
        target = self.path(day)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(".parquet.tmp")
        pq.write_table(table, staging, compression="zstd")
        staging.replace(target)
        return len(materialised)

    def read(self, day: date, symbol: str | None = None) -> list[IndexContractRow]:
        """The day's rows, or only one index's (filtered before the rows are built, which is most
        of the cost of reading a day)."""
        filters = None if symbol is None else [("symbol", "=", symbol)]
        table = pq.read_table(self.path(day), schema=_SCHEMA, filters=filters)
        return [self._row(record) for record in table.to_pylist()]

    def days(self) -> list[date]:
        return sorted(date.fromisoformat(p.stem) for p in self._root.glob("*/*.parquet"))

    @staticmethod
    def _row(record: dict[str, object]) -> IndexContractRow:
        right = record["right"]
        return IndexContractRow(
            day=record["day"],  # type: ignore[arg-type]
            symbol=str(record["symbol"]),
            kind=InstrumentKind(str(record["kind"])),
            expiry=record["expiry"],  # type: ignore[arg-type]
            strike=record["strike"],  # type: ignore[arg-type]
            right=None if right is None else OptionRight(str(right)),
            open=record["open"],  # type: ignore[arg-type]
            high=record["high"],  # type: ignore[arg-type]
            low=record["low"],  # type: ignore[arg-type]
            close=record["close"],  # type: ignore[arg-type]
            settle=record["settle"],  # type: ignore[arg-type]
            contracts=int(record["contracts"]),  # type: ignore[call-overload]
            turnover=record["turnover"],  # type: ignore[arg-type]
            open_interest=int(record["open_interest"]),  # type: ignore[call-overload]
            change_in_oi=int(record["change_in_oi"]),  # type: ignore[call-overload]
            underlying=record["underlying"],  # type: ignore[arg-type]
            lot_size=None if record["lot_size"] is None else int(record["lot_size"]),  # type: ignore[call-overload]
            source=ArchiveFormat(str(record["source"])),
        )


class FetchOutcome(StrEnum):
    FETCHED = "fetched"  # the file was downloaded and its index rows stored
    ABSENT = "absent"  # every candidate answered 404: no bhavcopy that day (a holiday)


@dataclass(frozen=True)
class LedgerEntry:
    day: date
    outcome: FetchOutcome
    url: str  # the URL that answered (FETCHED) or the last one tried (ABSENT)
    fetched_at: datetime
    size: int
    sha256: str
    rows: int
    source: ArchiveFormat | None

    def as_record(self) -> dict[str, object]:
        return {
            "day": self.day.isoformat(),
            "outcome": self.outcome.value,
            "url": self.url,
            "fetched_at": self.fetched_at.isoformat(),
            "size": self.size,
            "sha256": self.sha256,
            "rows": self.rows,
            "source": None if self.source is None else self.source.value,
        }

    @classmethod
    def from_record(cls, record: dict[str, object]) -> LedgerEntry:
        source = record["source"]
        return cls(
            date.fromisoformat(str(record["day"])),
            FetchOutcome(str(record["outcome"])),
            str(record["url"]),
            datetime.fromisoformat(str(record["fetched_at"])),
            int(str(record["size"])),
            str(record["sha256"]),
            int(str(record["rows"])),
            None if source is None else ArchiveFormat(str(source)),
        )


class FoLedger:
    """Append-only: a date is asked about once; a resumed run skips every date already here."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[LedgerEntry]:
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as handle:
            return [LedgerEntry.from_record(json.loads(line)) for line in handle if line.strip()]

    def done(self) -> set[date]:
        return {entry.day for entry in self.load()}

    def record(self, entry: LedgerEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.as_record(), sort_keys=True) + "\n")
