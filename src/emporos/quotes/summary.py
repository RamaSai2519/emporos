"""What one recorded day holds, for the operator: is the recorder really recording?"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from emporos.quotes.sink import read_day

__all__ = ["DaySummary", "summarize_day"]


@dataclass(frozen=True)
class DaySummary:
    day: date
    files: int
    rows: int
    instruments: int
    polls: int  # distinct receive times
    first_received: datetime | None
    last_received: datetime | None
    two_sided_share: float | None  # rows with both a bid and an ask
    median_spread_bps: float | None  # of the two-sided, uncrossed rows

    def lines(self) -> list[str]:
        if self.rows == 0:
            return [f"{self.day}: nothing recorded ({self.files} files)"]

        def num(value: float | None, unit: str = "") -> str:
            return "n/a" if value is None else f"{value:.2f}{unit}"

        share = None if self.two_sided_share is None else self.two_sided_share * 100
        return [
            f"{self.day}: {self.rows:,} rows in {self.files} files, "
            f"{self.instruments} instruments, {self.polls} polls",
            f"received {self.first_received} .. {self.last_received}",
            f"two-sided book {num(share, '%')}, "
            f"median spread {num(self.median_spread_bps, ' bps')}",
        ]


def summarize_day(root: Path, day: date) -> DaySummary:
    directory = root / f"date={day.isoformat()}"
    files = len(list(directory.glob("part-*.parquet"))) if directory.exists() else 0
    rows = read_day(root, day).to_pylist()
    if not rows:
        return DaySummary(day, files, 0, 0, 0, None, None, None, None)
    received = [r["received_at"] for r in rows]
    two_sided = [r for r in rows if r["bid"] is not None and r["ask"] is not None]
    spreads = [
        float((r["ask"] - r["bid"]) / ((r["ask"] + r["bid"]) / Decimal(2)) * Decimal(10_000))
        for r in two_sided
        if r["ask"] >= r["bid"] > 0
    ]
    return DaySummary(
        day,
        files,
        len(rows),
        len({r["instrument_id"] for r in rows}),
        len(set(received)),
        min(received),
        max(received),
        len(two_sided) / len(rows),
        median(spreads) if spreads else None,
    )
