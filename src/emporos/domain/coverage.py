"""What we know about how completely a trading day of history has been fetched (Phase 6).

Broker history is NOT the full session grid: even the most liquid stock is missing minutes
(recorded live: SBIN has 361 of 375 one-minute bars a day), so "a minute is missing" cannot mean
"data is missing". A `DayCoverage` records that a day was fetched from the broker and exactly which
minutes the broker did not return (`absent`). Gap detection subtracts those confirmed-absent
minutes, so a legitimately silent minute is never re-fetched forever while a bar that was stored
and later lost still shows up as a gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from emporos.domain.candles import Timeframe


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True, order=True)
class TimeRange:
    """A half-open interval `[start, end)` of UTC time."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_utc("start", self.start)
        _require_utc("end", self.end)
        if self.start >= self.end:
            raise ValueError("a time range must have start < end")

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True)
class DayCoverage:
    """`complete` means the whole session day was fetched (as opposed to one filled gap);
    `empty` means the broker returned no bars at all for a day we expected to trade."""

    instrument_id: str
    timeframe: Timeframe
    day: date  # the IST calendar date
    absent: tuple[TimeRange, ...]
    fetched_at: datetime
    complete: bool = True
    empty: bool = False

    def __post_init__(self) -> None:
        _require_utc("fetched_at", self.fetched_at)

    def is_absent(self, moment: datetime) -> bool:
        return any(r.contains(moment) for r in self.absent)
