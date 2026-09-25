"""The ledgers as Parquet (EM-243)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from emporos.research.atlas.crossings import CROSSING_COLUMNS, CrossingBlock
from emporos.research.atlas.events import FORWARD_SESSIONS, MoveEvent

__all__ = ["write_crossings", "write_moves"]


def write_moves(path: Path, events: Sequence[MoveEvent]) -> int:
    columns: dict[str, list[object]] = {
        "event_id": [e.event_id for e in events],
        "event_class": [e.event_class.value for e in events],
        "name": [e.name for e in events],
        "instrument_id": [e.instrument_id for e in events],
        "sector": [e.sector for e in events],
        "group": [e.group for e in events],
        "day": [e.day for e in events],
        "direction": [e.direction for e in events],
        "return_pct": [e.return_pct for e in events],
        "resid_pct": [e.resid_pct for e in events],
        "sigma_pct": [e.sigma_pct for e in events],
        "z": [e.z for e in events],
        "gap_resid_pct": [e.gap_resid_pct for e in events],
        "onset_kind": [e.onset_kind.value for e in events],
        "onset_at": [e.onset_at for e in events],
        "peak_at": [e.peak_at for e in events],
        "after_15m_pct": [e.after_15m_pct for e in events],
        "after_60m_pct": [e.after_60m_pct for e in events],
        "after_close_pct": [e.after_close_pct for e in events],
        "beta_market": [e.beta_market for e in events],
        "beta_sector": [e.beta_sector for e in events],
        "beta_group": [e.beta_group for e in events],
    }
    for j, h in enumerate(FORWARD_SESSIONS):
        columns[f"fwd_raw_{h}d_pct"] = [e.forward_raw_pct[j] for e in events]
        columns[f"fwd_resid_{h}d_pct"] = [e.forward_resid_pct[j] for e in events]
    _write(path, pa.table(columns))
    return len(events)


def write_crossings(path: Path, block: CrossingBlock) -> int:
    schema = {"day": pa.date32(), "direction": pa.int8(), "at_open": pa.bool_()}
    ints = {"crossed_minute": pa.int16(), "entry_minute": pa.int16()}
    strings = {"kind", "name", "instrument_id", "sector", "group"}
    arrays = []
    for name in CROSSING_COLUMNS:
        kind = (
            schema.get(name) or ints.get(name) or (pa.string() if name in strings else pa.float64())
        )
        arrays.append(pa.array(block.columns[name], type=kind))
    _write(path, pa.Table.from_arrays(arrays, names=list(CROSSING_COLUMNS)))
    return len(block)


def split_by_year(day: date, cut: date) -> bool:
    return day >= cut


def _write(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(path)
