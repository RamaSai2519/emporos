"""Business groups (Adani, Tata, ...) as the ledger needs them (EM-243, EM-244).

The map itself is hand-built with a source per row (Agent 1, EM-244); the ledger is written against
this Protocol. Membership is asked for as of a DAY, and the ledger asks for the day before, so a
name never joins a group index on the strength of what was known only later."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Protocol

__all__ = ["GroupMap", "NoGroups", "StaticGroups"]


class GroupMap(Protocol):
    def group_of(self, symbol: str) -> str | None: ...

    def members(self, group: str, as_of: date) -> frozenset[str]:
        """The group's trading symbols on that day."""
        ...


class NoGroups:
    """No group term: every name is outside every group."""

    def group_of(self, symbol: str) -> str | None:
        return None

    def members(self, group: str, as_of: date) -> frozenset[str]:
        return frozenset()


class StaticGroups:
    """A map that does not change over time (tests, and the first cut of the real one)."""

    def __init__(self, groups: Mapping[str, frozenset[str]]) -> None:
        self._groups = dict(groups)
        self._of = {s: g for g, names in self._groups.items() for s in names}

    def group_of(self, symbol: str) -> str | None:
        return self._of.get(symbol)

    def members(self, group: str, as_of: date) -> frozenset[str]:
        return self._groups.get(group, frozenset())
