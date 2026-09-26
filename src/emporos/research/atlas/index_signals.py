"""Index crossings as SIGNALS ONLY: NIFTY and BANKNIFTY (EM-248 E2, declaration
`s1-size-target-trail`; EM-243 crossing rule).

The same code path as the Crossing Ledger's `index` kind: the index's own 5-minute return path
against `k x sigma15`, the first bar per session and direction (`CrossingScanner.intraday` on a
`Decomposition` with no regressors). What is kept is the decision only (who, day, direction, k,
whether it was already there at the open, the crossing bar's end): no forward return and no drift
leaves this module, in the file or in the report. The counts and the onset timing are what the
report shows."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from emporos.research.atlas.crossings import CrossingRules, CrossingScanner
from emporos.research.atlas.decompose import Decomposer
from emporos.research.atlas.panel import InstrumentPanel
from emporos.research.atlas.returns import returns_of

__all__ = ["IndexCrossing", "IndexSignalBuilder", "index_signal_lines", "write_index_signals"]

HALF_HOUR = 30
FIRST_MINUTE = 9 * 60 + 15


@dataclass(frozen=True)
class IndexCrossing:
    name: str
    instrument_id: str
    day: date
    direction: int
    k: float
    at_open: bool
    crossed_minute: int  # the crossing bar's end, minutes after midnight IST


class IndexSignalBuilder:
    def __init__(
        self,
        sessions: tuple[date, ...],
        rules: CrossingRules | None = None,
        decomposer: Decomposer | None = None,
    ) -> None:
        self._scanner = CrossingScanner(sessions, rules)
        self._decomposer = decomposer or Decomposer()

    def build(
        self,
        indices: Mapping[str, str],  # name -> instrument id
        panels: Mapping[str, InstrumentPanel],  # by instrument id
    ) -> list[IndexCrossing]:
        found: list[IndexCrossing] = []
        for name, instrument_id in sorted(indices.items()):
            own = returns_of(panels[instrument_id])
            deco = self._decomposer.decompose(own, [])
            block = self._scanner.intraday((name, instrument_id, "", ""), own, deco, "index")
            cols = block.columns
            for i in range(len(block)):
                found.append(
                    IndexCrossing(
                        name,
                        instrument_id,
                        cols["day"][i],  # type: ignore[arg-type]
                        int(cols["direction"][i]),  # type: ignore[call-overload]
                        float(cols["k"][i]),  # type: ignore[arg-type]
                        bool(cols["at_open"][i]),
                        int(cols["crossed_minute"][i]),  # type: ignore[call-overload]
                    )  # fmt: skip
                )
        return sorted(found, key=lambda c: (c.day, c.crossed_minute, c.name, c.direction, c.k))


def write_index_signals(path: Path, crossings: Sequence[IndexCrossing]) -> int:
    table = pa.table(
        {
            "name": pa.array([c.name for c in crossings], pa.string()),
            "instrument_id": pa.array([c.instrument_id for c in crossings], pa.string()),
            "day": pa.array([c.day for c in crossings], pa.date32()),
            "direction": pa.array([c.direction for c in crossings], pa.int8()),
            "k": pa.array([c.k for c in crossings], pa.float64()),
            "at_open": pa.array([c.at_open for c in crossings], pa.bool_()),
            "crossed_minute": pa.array([c.crossed_minute for c in crossings], pa.int16()),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(path)
    return len(crossings)


PERIODS = (
    ("2017-11..2023-12", date(2017, 11, 1), date(2023, 12, 31)),
    ("2024", date(2024, 1, 1), date(2024, 12, 31)),
    ("2025-01..2026-03", date(2025, 1, 1), date(2026, 3, 31)),
)


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def index_signal_lines(crossings: Sequence[IndexCrossing]) -> list[str]:
    """Counts per month and index, and the onset timing per period: counts and clock times only."""
    names = sorted({c.name for c in crossings})
    keys = sorted({(c.name, c.k) for c in crossings})
    lines = [
        f"Index crossings (signals only, no forward figures): {len(crossings):,} in all",
        "",
        "Per month: " + ", ".join(f"{n}@{k}" for n, k in keys),
    ]
    monthly: Counter[tuple[str, tuple[str, float]]] = Counter(
        (f"{c.day.year}-{c.day.month:02d}", (c.name, c.k)) for c in crossings
    )
    lines.append("  month    " + "".join(f"{f'{n}@{k}':>16}" for n, k in keys))
    for month in sorted({m for m, _ in monthly}):
        lines.append(f"  {month}  " + "".join(f"{monthly[(month, key)]:>16,}" for key in keys))
    lines += ["", "Onset (the crossing bar's end), share of crossings by half hour, per period:"]
    for label, first, last in PERIODS:
        for name in names:
            rows = [
                c for c in crossings if c.name == name and first <= c.day <= last and c.k == 1.5
            ]
            if not rows:
                continue
            opening = sum(1 for c in rows if c.at_open) / len(rows)
            buckets = Counter((c.crossed_minute - FIRST_MINUTE) // HALF_HOUR for c in rows)
            cells = "  ".join(
                f"{_clock(FIRST_MINUTE + b * HALF_HOUR)} {buckets[b] / len(rows):.0%}"
                for b in sorted(buckets)
            )
            lines.append(
                f"  {label:<17} {name:<10} n={len(rows):>5,} at open {opening:.0%}  {cells}"
            )
    return lines
