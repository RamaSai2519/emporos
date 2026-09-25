"""Business groups (Track R, driver-atlas-plan §3.2 item 5, EM-244).

A group is a set of D1 names under one promoter family or parent, hand-built with a source page per
row. Membership has dates: a name is a member on `as_of` if `from <= as_of` and (`to` is missing or
`as_of < to`). Point-in-time rule (§3.3): the group index and the peer set for a session are built
from membership AS OF THE DAY BEFORE, so `*_for_session` takes the session day and reads the day
before it; a change effective on the session day itself is never seen by that session's open."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import yaml

__all__ = ["DEFAULT_GROUPS_FILE", "GroupMap", "GroupMember", "YamlGroupMap"]

DEFAULT_GROUPS_FILE = Path("config/universe/track-r/business_groups.yaml")


class GroupMap(Protocol):
    def group_ids(self, as_of: date) -> tuple[str, ...]:
        """The groups with at least two members on `as_of`."""
        ...

    def members(self, group_id: str, as_of: date) -> tuple[str, ...]:
        """The trading symbols in the group on `as_of`, sorted."""
        ...

    def groups_of(self, symbol: str, as_of: date) -> tuple[str, ...]: ...

    def peers(self, symbol: str, as_of: date) -> tuple[str, ...]:
        """The other members of every group the symbol is in on `as_of`, sorted, without repeats."""
        ...

    def members_for_session(self, group_id: str, session: date) -> tuple[str, ...]:
        """`members` as of the day before `session`."""
        ...

    def peers_for_session(self, symbol: str, session: date) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class GroupMember:
    group_id: str
    symbol: str
    first: date | None  # inclusive
    last_exclusive: date | None
    source: str
    checked_on: date

    def is_member(self, as_of: date) -> bool:
        return (self.first is None or self.first <= as_of) and (
            self.last_exclusive is None or as_of < self.last_exclusive
        )


class YamlGroupMap:
    def __init__(self, members: Iterable[GroupMember]) -> None:
        self._rows = tuple(members)
        for row in self._rows:
            if not row.source.startswith("http"):
                raise ValueError(f"{row.group_id}/{row.symbol}: every row needs a source URL")
            if row.first and row.last_exclusive and row.first >= row.last_exclusive:
                raise ValueError(f"{row.group_id}/{row.symbol}: `from` is not before `to`")

    @classmethod
    def load(cls, path: Path = DEFAULT_GROUPS_FILE) -> YamlGroupMap:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows: list[GroupMember] = []
        for group in document["groups"]:
            for member in group["members"]:
                rows.append(
                    GroupMember(
                        group["id"],
                        member["symbol"],
                        member.get("from"),
                        member.get("to"),
                        member["source"],
                        member["checked_on"],
                    )  # fmt: skip
                )
        return cls(rows)

    def group_ids(self, as_of: date) -> tuple[str, ...]:
        return tuple(
            g for g in sorted({r.group_id for r in self._rows}) if len(self.members(g, as_of)) >= 2
        )

    def members(self, group_id: str, as_of: date) -> tuple[str, ...]:
        return tuple(
            sorted(r.symbol for r in self._rows if r.group_id == group_id and r.is_member(as_of))
        )

    def groups_of(self, symbol: str, as_of: date) -> tuple[str, ...]:
        return tuple(
            sorted({r.group_id for r in self._rows if r.symbol == symbol and r.is_member(as_of)})
        )

    def peers(self, symbol: str, as_of: date) -> tuple[str, ...]:
        found = {other for g in self.groups_of(symbol, as_of) for other in self.members(g, as_of)}
        return tuple(sorted(found - {symbol}))

    def members_for_session(self, group_id: str, session: date) -> tuple[str, ...]:
        return self.members(group_id, session - timedelta(days=1))

    def peers_for_session(self, symbol: str, session: date) -> tuple[str, ...]:
        return self.peers(symbol, session - timedelta(days=1))

    def symbols(self) -> frozenset[str]:
        return frozenset(r.symbol for r in self._rows)
