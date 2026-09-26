"""Insider and SAST rows as Parquet, built from the raw monthly replies (EM-244).

`InsiderLedger` reads every raw month under `<raw>/pit` and `<raw>/sast` (kept verbatim by the page
collector) and writes `pit.parquet` and `sast.parquet` under the dataset directory, sorted by
`published_at`, one row per disclosure (the same disclosure in two overlapping months is kept
once). Rows dated outside the requested span are dropped."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import astuple, dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from emporos.core.clock import IST
from emporos.research.cause_ledger.insider import (
    InsiderTrade,
    SastDisclosure,
    parse_pit,
    parse_sast,
)

__all__ = ["InsiderLedger", "InsiderSummary"]

_TYPES: dict[str, pa.DataType] = {
    "str": pa.string(), "int": pa.int64(), "float": pa.float64(), "bool": pa.bool_(),
    "date": pa.date32(), "datetime": pa.timestamp("us", tz="Asia/Kolkata"),
}  # fmt: skip


@dataclass(frozen=True)
class InsiderSummary:
    pit_rows: int
    sast_rows: int
    first: datetime | None
    last: datetime | None


def _schema(row_type: type) -> pa.Schema:
    out = []
    for f in fields(row_type):
        name = str(f.type).replace(" | None", "").replace("'", "")
        out.append((f.name, _TYPES[name]))
    return pa.schema(out)


class InsiderLedger:
    def __init__(self, raw_root: Path, out_dir: Path) -> None:
        self._raw, self._out = raw_root, out_dir

    def build(self, first: date, last: date) -> InsiderSummary:
        pit = self._read("pit", parse_pit, first, last)
        sast = self._read("sast", parse_sast, first, last)
        self._write("pit.parquet", InsiderTrade, pit)
        self._write("sast.parquet", SastDisclosure, sast)
        stamps = sorted(r.published_at for r in [*pit, *sast])
        return InsiderSummary(
            len(pit), len(sast), stamps[0] if stamps else None, stamps[-1] if stamps else None
        )

    def _read(
        self, folder: str, parse: Callable[[bytes], Sequence[Any]], first: date, last: date
    ) -> list[Any]:
        rows: dict[tuple[Any, ...], Any] = {}
        for path in sorted((self._raw / folder).glob("*.json")):
            for row in parse(path.read_bytes()):
                if first <= row.published_at.astimezone(IST).date() <= last:
                    rows[astuple(row)] = row
        return sorted(rows.values(), key=lambda r: (r.published_at, r.symbol))

    def _write(self, name: str, row_type: type, rows: Sequence[Any]) -> None:
        schema = _schema(row_type)
        columns = {f.name: [getattr(r, f.name) for r in rows] for f in fields(row_type)}
        self._out.mkdir(parents=True, exist_ok=True)
        staging = self._out / f"{name}.tmp"
        pq.write_table(pa.Table.from_pydict(columns, schema=schema), staging, compression="zstd")
        staging.replace(self._out / name)

    def read_pit(self) -> list[InsiderTrade]:
        return [InsiderTrade(**r) for r in pq.read_table(self._out / "pit.parquet").to_pylist()]

    def read_sast(self) -> list[SastDisclosure]:
        return [SastDisclosure(**r) for r in pq.read_table(self._out / "sast.parquet").to_pylist()]
