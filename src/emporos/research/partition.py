"""The data partition every result is judged on (EDGE_SEARCH_PLAN.md §4.3).

Fixed numbers, pinned by a test like the screen bar: moving one is the operator's commit, not a
convenience. Discovery is what S1 and S2 (and walk-forward training) may read. Confirmation is the
declared holdout S3 reads and the walk-forward test windows S4 reads. The vault is sealed
separately (`emporos.backtest.vault`) and no stage here can name it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

__all__ = ["DISCOVERY", "CONFIRMATION", "DataSplit"]


@dataclass(frozen=True)
class DataSplit:
    name: str
    first: date
    last: date  # inclusive IST day

    def __post_init__(self) -> None:
        if self.first > self.last:
            raise ValueError("a split's first day is after its last")

    def contains(self, day: date) -> bool:
        return self.first <= day <= self.last


# "start of data": the ten-year history begins 2016-10-03 (docs/data/history.md)
DISCOVERY = DataSplit("discovery", date(2016, 10, 3), date(2024, 12, 31))
CONFIRMATION = DataSplit("confirmation", date(2025, 1, 1), date(2026, 3, 18))
