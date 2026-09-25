"""As-of index membership, rebuilt from today's constituents and the changes since (EM-224, A-F3).

D1 is today's NIFTY 100 and NIFTY Midcap 150. Projected back in time it contains names that were not
in either index then, which flatters a long-only rule (PROFIT_PLAN §2.5). If every change to an
index is on record, its membership on any past day is today's set with those changes undone, newest
first: a name ADDED after the day was not a member on it; a name REMOVED after the day was.

`MembershipTimeline` does that for one index and keeps what it could not reconcile:
* an undone ADD of a name that is not a member at that point, or an undone REMOVE of a name that
  already is, means a change is missing or wrong: it is listed in `inconsistencies`, and the
  timeline is only as trustworthy as that list is short;
* `coverage_start` is the earliest change on record. Before it nothing is known (a name added and
  removed before the first record leaves no trace), so the timeline answers with the earliest
  reconstruction and says so; a caller that needs certainty starts its window at `coverage_start`.

`AsOfMembership` is the union of several timelines (D1 is two indices) and satisfies the swing
screener's `Membership` protocol, keyed by instrument id through a symbol table.

The file (`config/universe/d1/index-changes.yaml`) is data with a source and fetch date per change;
this module fetches nothing.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from emporos.core.errors import ConfigurationError

__all__ = [
    "DEFAULT_CHANGES", "AsOfMembership", "IndexChange", "MembershipTimeline", "load_changes",
]  # fmt: skip

DEFAULT_CHANGES = Path("config/universe/d1/index-changes.yaml")


@dataclass(frozen=True)
class IndexChange:
    index: str
    effective: date  # the first session the change applies to
    added: frozenset[str]
    removed: frozenset[str]
    source: str  # where it came from and when it was fetched

    def __post_init__(self) -> None:
        if not self.index.strip() or not self.source.strip():
            raise ValueError("a change names its index and its source")
        if not (self.added or self.removed):
            raise ValueError(f"{self.index} {self.effective}: a change adds or removes a name")
        if self.added & self.removed:
            raise ValueError(
                f"{self.index} {self.effective}: a name cannot be both added and removed"
            )


class MembershipTimeline:
    def __init__(self, index: str, current: Iterable[str], changes: Iterable[IndexChange]) -> None:
        mine = sorted((c for c in changes if c.index == index), key=lambda c: c.effective)
        self._index = index
        self._starts: list[date] = []  # ascending: the days from which a set applies
        self._sets: list[frozenset[str]] = []
        self.inconsistencies: list[str] = []
        members = set(current)
        after: list[tuple[date, frozenset[str]]] = [(date.max, frozenset(members))]
        for change in reversed(mine):
            for name in sorted(change.added):
                if name not in members:
                    self.inconsistencies.append(
                        f"{index} {change.effective}: {name} was added but is not a member after it"
                    )
                members.discard(name)
            for name in sorted(change.removed):
                if name in members:
                    self.inconsistencies.append(
                        f"{index} {change.effective}: {name} was removed but is a member after it"
                    )
                members.add(name)
            after.append((change.effective, frozenset(members)))
        # `after[k]` is the membership BEFORE the change dated after[k][0]
        for effective, before in reversed(after[1:]):
            self._starts.append(effective)
            self._sets.append(before)
        self._current = frozenset(current)
        self._coverage_start = mine[0].effective if mine else None

    @property
    def index(self) -> str:
        return self._index

    @property
    def coverage_start(self) -> date | None:
        """The earliest change on record; None when there are none (then nothing is known)."""
        return self._coverage_start

    def members_on(self, day: date) -> frozenset[str]:
        """Members on `day`: the set from before the first change whose date is after `day`."""
        position = bisect_right(self._starts, day)
        if position == len(self._starts):
            return self._current
        return self._sets[position]


class AsOfMembership:
    def __init__(
        self, timelines: Sequence[MembershipTimeline], instruments: Mapping[str, str]
    ) -> None:
        """`instruments` maps a symbol to its instrument id; a symbol with none is not tradable."""
        self._timelines = tuple(timelines)
        self._ids = dict(instruments)

    @property
    def coverage_start(self) -> date | None:
        starts = [t.coverage_start for t in self._timelines]
        return None if any(s is None for s in starts) else max(s for s in starts if s is not None)

    @property
    def inconsistencies(self) -> list[str]:
        return [line for t in self._timelines for line in t.inconsistencies]

    def symbols_on(self, day: date) -> frozenset[str]:
        return frozenset().union(*(t.members_on(day) for t in self._timelines))

    def is_member(self, instrument_id: str, day: date) -> bool:
        return any(self._ids.get(symbol) == instrument_id for symbol in self.symbols_on(day))


def load_changes(path: Path = DEFAULT_CHANGES) -> list[IndexChange]:
    """The recorded changes. An absent or empty file is an empty list: nothing is on record."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        unknown = set(raw) - {"changes"}
        if unknown:
            raise ValueError(f"unknown keys {sorted(unknown)}")
        return [_change(entry) for entry in raw.get("changes") or []]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ConfigurationError(f"{path} is not an index-change file: {error}") from error


def _change(entry: Mapping[str, Any]) -> IndexChange:
    unknown = set(entry) - {"index", "effective", "added", "removed", "source"}
    if unknown:
        raise ValueError(f"unknown keys {sorted(unknown)} in {dict(entry)}")
    return IndexChange(
        str(entry["index"]),
        date.fromisoformat(str(entry["effective"])),
        frozenset(str(s) for s in entry.get("added") or []),
        frozenset(str(s) for s in entry.get("removed") or []),
        str(entry["source"]),
    )
