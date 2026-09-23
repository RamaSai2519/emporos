"""Corporate-action quarantine (EM-177): unadjusted split/bonus/dividend artifacts a backtest must
not silently trade across.

Prices are stored UNADJUSTED (see `history/quality.py`); `OvernightDiscontinuity` is how one shows
up in the data. Detecting a discontinuity is not the same as knowing it is a genuine corporate
action rather than bad data, so a quarantine entry always carries its `source`: `DETECTED` means a
check flagged it and a person has not yet confirmed or adjusted the series; `CURATED` means a person
reviewed it and is quarantining it deliberately (with or without a later adjustment). Either way, a
quarantined instrument/day pair must not be used unadjusted by a backtest without an explicit
override — see `backtest/integrity.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol


class QuarantineSource(StrEnum):
    DETECTED = "detected"  # a data-quality check flagged it; unreviewed
    CURATED = "curated"  # a person reviewed and confirmed it


@dataclass(frozen=True)
class QuarantineEntry:
    instrument_id: str
    day: date
    reason: str
    source: QuarantineSource
    recorded_at: datetime


class CorporateActionQuarantine:
    """An immutable snapshot of every quarantined instrument/day pair."""

    def __init__(self, entries: Sequence[QuarantineEntry] = ()) -> None:
        self._entries = tuple(entries)
        self._by_instrument: dict[str, set[date]] = {}
        for entry in self._entries:
            self._by_instrument.setdefault(entry.instrument_id, set()).add(entry.day)

    def is_quarantined(self, instrument_id: str, day: date) -> bool:
        return day in self._by_instrument.get(instrument_id, ())

    def overlapping(
        self, instrument_id: str, first: date, last: date
    ) -> tuple[QuarantineEntry, ...]:
        return tuple(
            e for e in self._entries if e.instrument_id == instrument_id and first <= e.day <= last
        )

    def all_entries(self) -> tuple[QuarantineEntry, ...]:
        return self._entries

    def __len__(self) -> int:
        return len(self._entries)


class QuarantineStore(Protocol):
    async def load_all(self) -> tuple[QuarantineEntry, ...]: ...

    async def add(self, entries: Sequence[QuarantineEntry]) -> None: ...


class QuarantineCurator:
    """Turns a quality audit's `overnight_discontinuity` findings into `DETECTED` entries, skipping
    anything already quarantined. Pure: no I/O, so a caller decides whether and when to persist."""

    def propose(
        self,
        findings: Sequence[tuple[str, date, str]],  # (instrument_id, day, detail)
        existing: CorporateActionQuarantine,
        recorded_at: datetime,
    ) -> tuple[QuarantineEntry, ...]:
        return tuple(
            QuarantineEntry(instrument_id, day, detail, QuarantineSource.DETECTED, recorded_at)
            for instrument_id, day, detail in findings
            if not existing.is_quarantined(instrument_id, day)
        )
