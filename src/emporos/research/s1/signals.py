"""Entry signals from the crossing ledger (Track R, EM-243) for the S1 stock book."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

__all__ = ["Signal", "load_stock_signals", "signals_by_day"]


@dataclass(frozen=True)
class Signal:
    instrument_id: str
    name: str
    day: date
    minute: int  # the crossing bar's end, minutes after midnight IST: the decision moment
    direction: int  # +1 up (buy), -1 down (sell short, intraday)


def load_stock_signals(path: Path, first: date, last: date, k: float = 1.5) -> list[Signal]:
    """Every stock crossing at level `k` in [first, last], in time order."""
    table = pq.read_table(
        path, columns=["kind", "k", "day", "name", "instrument_id", "direction", "crossed_minute"]
    )
    keep = pc.and_(
        pc.equal(table["kind"], "stock"),
        pc.and_(
            pc.equal(table["k"], k),
            pc.and_(pc.greater_equal(table["day"], first), pc.less_equal(table["day"], last)),
        ),
    )
    rows = table.filter(keep).to_pylist()
    signals = [
        Signal(
            r["instrument_id"], r["name"], r["day"], int(r["crossed_minute"]), int(r["direction"])
        )
        for r in rows
    ]
    return sorted(signals, key=lambda s: (s.day, s.minute, s.name, s.direction))


def signals_by_day(signals: list[Signal]) -> dict[date, list[Signal]]:
    grouped: dict[date, list[Signal]] = defaultdict(list)
    for signal in signals:
        grouped[signal.day].append(signal)
    return dict(grouped)
