"""The universe as it was on the day (plan.md §10 survivorship).

The instrument master keeps history: the CURRENT definition of an instrument carries `valid_from`,
and every superseded or removed definition sits in `instrument_versions` as a closed
`[valid_from, valid_to)`. Resolving symbols against the master AS OF the run's first day, rather
than today's, keeps names that were later delisted, and uses the symbol a stock had then.

`AsOfInstruments.as_of(moment)` builds the resolver for that moment from eras. An instrument with
no era covering the moment is simply absent, so a config naming it fails loudly.

The one honest gap: the master only began recording history when it was first synced. For a moment
BEFORE an instrument's earliest recorded era, nothing says what it looked like. That is refused by
default; `assume_earliest_before_history=True` opts in to using its earliest known definition, and
the ids that relied on it come back in `assumed_ids` so the report can say so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from emporos.domain.instruments import Instrument
from emporos.instruments.cache import InstrumentCache


@dataclass(frozen=True)
class InstrumentEra:
    """One definition of an instrument and when it was in force (`valid_to` None = still is)."""

    instrument: Instrument
    valid_from: datetime
    valid_to: datetime | None

    def __post_init__(self) -> None:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("an era must end after it starts")

    def covers(self, moment: datetime) -> bool:
        return self.valid_from <= moment and (self.valid_to is None or moment < self.valid_to)


@dataclass(frozen=True)
class AsOfUniverse:
    resolver: InstrumentCache
    moment: datetime
    assumed_ids: frozenset[str]  # present only through the earliest-known-definition fallback


class AsOfInstruments:
    def __init__(self, eras: Sequence[InstrumentEra]) -> None:
        self._by_id: dict[str, list[InstrumentEra]] = {}
        for era in eras:
            self._by_id.setdefault(era.instrument.instrument_id, []).append(era)
        for history in self._by_id.values():
            history.sort(key=lambda e: e.valid_from)

    def as_of(
        self, moment: datetime, *, assume_earliest_before_history: bool = False
    ) -> AsOfUniverse:
        present: list[Instrument] = []
        assumed: set[str] = set()
        for instrument_id, history in self._by_id.items():
            covering = [era for era in history if era.covers(moment)]
            if covering:
                present.append(covering[-1].instrument)
            elif assume_earliest_before_history and moment < history[0].valid_from:
                present.append(history[0].instrument)
                assumed.add(instrument_id)
        return AsOfUniverse(InstrumentCache(present), moment, frozenset(assumed))
