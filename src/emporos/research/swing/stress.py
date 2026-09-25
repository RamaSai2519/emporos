"""Survivorship stress for a frozen candidate: three ways to take today's-constituents bias out of
the equity universe (EM-235).

D1 is today's NIFTY 100 and NIFTY Midcap 150. A name that grew into them is in the universe for
years it was not, which flatters a long-only rule and its equal-weight benchmark alike. Each
`UniverseStress` returns the universe a stressed run trades, and applies to the book AND its
benchmark, which are built from the same result:

* `LateListedOut`: drop every name whose first daily session is after the cutoff (it did not exist
  as a listed name for the whole window).
* `LateJoinersOut`: that, and also every name that ENTERED the two indices during the window
  (`late_joiners`, from the parsed notices, however incomplete they are).
* `AsOf`: every name stays in the data but is tradable only while a member of NIFTY 100 or Midcap
  150 (`AsOfMembership`), the true point-in-time universe.

Nothing here decides anything: it hands the run a universe and says what it removed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from emporos.research.index_membership import AsOfMembership
from emporos.research.swing.data import SwingDataset
from emporos.research.swing.rules import Membership

__all__ = [
    "AsOf", "LateJoinersOut", "LateListedOut", "StressedUniverse", "UniverseStress",
    "late_joiners", "late_listed",
]  # fmt: skip


@dataclass(frozen=True)
class StressedUniverse:
    dataset: SwingDataset
    membership: Membership | None
    removed: Mapping[str, str]  # instrument id -> why it is out


class UniverseStress(Protocol):
    name: str

    def apply(self, dataset: SwingDataset) -> StressedUniverse: ...


def late_listed(dataset: SwingDataset, cutoff: date) -> dict[str, date]:
    """Instrument id -> its first session, for every name whose first session is after `cutoff`."""
    firsts = {i: dataset.series(i).days[0] for i in dataset.instrument_ids}
    return {i: d for i, d in firsts.items() if d > cutoff}


def late_joiners(
    membership: AsOfMembership, symbols: Mapping[str, str], first: date, last: date,
    change_days: Sequence[date],
) -> dict[str, date]:  # fmt: skip
    """Instrument id -> the day it entered the union of the two indices in (first, last]. A name
    that only moved between the two indices was already in the universe and is not a joiner."""
    joined: dict[str, date] = {}
    for day in sorted({d for d in change_days if first < d <= last}):
        before = membership.symbols_on(day - timedelta(days=1))
        for symbol in sorted(membership.symbols_on(day) - before):
            instrument = symbols.get(symbol)
            if instrument is not None:
                joined.setdefault(instrument, day)
    return joined


class LateListedOut:
    name = "late-listed-out"

    def __init__(self, cutoff: date) -> None:
        self._cutoff = cutoff

    def apply(self, dataset: SwingDataset) -> StressedUniverse:
        gone = late_listed(dataset, self._cutoff)
        keep = [i for i in dataset.instrument_ids if i not in gone]
        why = {i: f"first daily session {d} is after {self._cutoff}" for i, d in gone.items()}
        return StressedUniverse(dataset.subset(keep), None, why)


class LateJoinersOut:
    name = "late-joiners-out"

    def __init__(self, cutoff: date, joiners: Mapping[str, date]) -> None:
        self._listed = LateListedOut(cutoff)
        self._joiners = dict(joiners)

    def apply(self, dataset: SwingDataset) -> StressedUniverse:
        first = self._listed.apply(dataset)
        why = dict(first.removed)
        for instrument, day in self._joiners.items():
            if instrument in dataset.instrument_ids:
                why.setdefault(instrument, f"entered NIFTY 100 / Midcap 150 on {day}")
        keep = [i for i in dataset.instrument_ids if i not in why]
        return StressedUniverse(dataset.subset(keep), None, why)


class AsOf:
    name = "as-of-membership"

    def __init__(self, membership: AsOfMembership) -> None:
        self._membership = membership

    def apply(self, dataset: SwingDataset) -> StressedUniverse:
        return StressedUniverse(dataset, self._membership, {})
