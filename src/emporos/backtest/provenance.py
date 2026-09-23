"""Research provenance (EM-177): what data a backtest actually ran against, content-hashed.

`ConfigSnapshotter` (strategies/snapshot.py) versions WHAT a run's strategy config was.
`ProvenanceSnapshotter` versions WHAT DATA it ran against: the resolved universe, the trading
calendar it was checked against, and the corporate-action quarantine state in force — each hashed
the same way (SHA-256 over canonical JSON), so two runs claiming the same dataset can be proven
identical or different, and a stale run can be told apart from a fresh one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from emporos.backtest.universe import AsOfUniverse
from emporos.core.hashing import Canonical, content_hash
from emporos.domain.candles import Timeframe
from emporos.history.quarantine import CorporateActionQuarantine

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ResearchProvenance:
    schema_version: int
    dataset_timeframe: Timeframe
    dataset_first: date
    dataset_last: date
    universe_hash: str
    calendar_version: str
    quarantine_hash: str
    assumed_instrument_ids: tuple[str, ...]


class ProvenanceSnapshotter:
    """`instrument_ids` is the run's OWN universe — the strategy config's resolved instruments, not
    every instrument the as-of resolver happens to carry (the resolver spans the whole instrument
    master, which a two-symbol strategy never touches)."""

    def take(
        self,
        universe: AsOfUniverse,
        instrument_ids: Iterable[str],
        timeframe: Timeframe,
        first: date,
        last: date,
        quarantine: CorporateActionQuarantine,
        calendar_version: str,
    ) -> ResearchProvenance:
        ids = frozenset(instrument_ids)
        return ResearchProvenance(
            schema_version=SCHEMA_VERSION,
            dataset_timeframe=timeframe,
            dataset_first=first,
            dataset_last=last,
            universe_hash=content_hash(self._universe_document(universe, ids)),
            calendar_version=calendar_version,
            quarantine_hash=content_hash(self._quarantine_document(quarantine)),
            assumed_instrument_ids=tuple(sorted(ids & universe.assumed_ids)),
        )

    @staticmethod
    def _universe_document(universe: AsOfUniverse, ids: frozenset[str]) -> dict[str, Canonical]:
        instrument_ids: list[Canonical] = [i for i in sorted(ids)]
        return {"moment": universe.moment.isoformat(), "instrument_ids": instrument_ids}

    @staticmethod
    def _quarantine_document(quarantine: CorporateActionQuarantine) -> dict[str, Canonical]:
        entries: list[Canonical] = [
            {"instrument_id": e.instrument_id, "day": e.day.isoformat(), "source": e.source.value}
            for e in sorted(quarantine.all_entries(), key=lambda e: (e.instrument_id, e.day))
        ]
        return {"entries": entries}
