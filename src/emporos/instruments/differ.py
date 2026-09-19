"""Diff a validated new instrument master against the current one."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from emporos.domain.instruments import Instrument


@dataclass(frozen=True)
class InstrumentChange:
    before: Instrument
    after: Instrument


@dataclass(frozen=True)
class InstrumentDiff:
    added: tuple[Instrument, ...] = ()
    changed: tuple[InstrumentChange, ...] = ()
    removed: tuple[Instrument, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    def summary(self) -> str:
        return f"{len(self.added)} added, {len(self.changed)} changed, {len(self.removed)} removed"


class InstrumentDiffer:
    """Compares by `instrument_id`; any difference in a tracked field is a change.

    Tracked fields are everything but identity: tradingsymbol, name, lot size and tick size.
    """

    def diff(self, current: Iterable[Instrument], new: Iterable[Instrument]) -> InstrumentDiff:
        before = {i.instrument_id: i for i in current}
        after = {i.instrument_id: i for i in new}
        return InstrumentDiff(
            added=tuple(after[key] for key in sorted(after.keys() - before.keys())),
            changed=tuple(
                InstrumentChange(before[key], after[key])
                for key in sorted(before.keys() & after.keys())
                if before[key] != after[key]
            ),
            removed=tuple(before[key] for key in sorted(before.keys() - after.keys())),
        )
