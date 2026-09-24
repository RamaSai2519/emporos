"""The edge-search map (EM-191 / EDGE_SEARCH_PLAN.md §4.1 F1, §5): every lane, every cell.

`docs/research/edge-search/search-map.yaml` is the program's resume point and its audit of what has
been ruled out. It is read as strictly as an experiment declaration: unknown keys are refused, every
reference (a cell's lane, parent and required foundations) must resolve, a parent chain may not
loop, and a cell that reached a verdict must carry its one-line lesson. A malformed map fails CI,
so the loop can never resume from a map that silently lost a cell.

`SearchMap.is_exhausted` is the plan's EXHAUSTED condition (§0): every cell is terminal — rejected
with a lesson, or blocked on a named Jira key.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, TypeVar

import yaml

from emporos.core.errors import ConfigurationError

__all__ = [
    "CellStatus", "Foundation", "FoundationStatus", "Lane", "LanePriority", "SearchCell",
    "SearchMap", "SearchMapLoader", "Status",
]  # fmt: skip

_JIRA_KEY = re.compile(r"^EM-\d+$")
_BLOCKED = re.compile(r"^BLOCKED\((?P<key>[^)]*)\)$")
_CELL_ID = re.compile(r"^L\d+-[a-z0-9]+(?:-[a-z0-9]+)*$")
_SLUG = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


class CellStatus(StrEnum):
    TODO = "TODO"
    INFEASIBLE = "INFEASIBLE"
    SCREEN_REJECT = "SCREEN_REJECT"
    CONFIRM_REJECT = "CONFIRM_REJECT"
    CURATE_REJECT = "CURATE_REJECT"
    VAULT_REJECT = "VAULT_REJECT"
    PAPER_REJECT = "PAPER_REJECT"
    BLOCKED = "BLOCKED"
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"

    @property
    def is_rejection(self) -> bool:
        return self in _REJECTIONS

    @property
    def is_terminal(self) -> bool:
        """Counts toward EXHAUSTED: a verdict was reached, or the cell waits on someone else."""
        return self.is_rejection or self in (CellStatus.BLOCKED, CellStatus.VALIDATED)


_REJECTIONS = frozenset(
    {
        CellStatus.INFEASIBLE, CellStatus.SCREEN_REJECT, CellStatus.CONFIRM_REJECT,
        CellStatus.CURATE_REJECT, CellStatus.VAULT_REJECT, CellStatus.PAPER_REJECT,
    }
)  # fmt: skip


class FoundationStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    BLOCKED = "BLOCKED"


S = TypeVar("S", CellStatus, FoundationStatus)


class LanePriority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


@dataclass(frozen=True)
class Status(Generic[S]):  # the pinned mypy predates PEP 695 generics
    """A status plus, for BLOCKED, the Jira key it waits on — written `BLOCKED(EM-123)`."""

    value: S
    blocked_by: str | None = None

    def __post_init__(self) -> None:
        blocked = self.value.value == "BLOCKED"
        if blocked and (self.blocked_by is None or not _JIRA_KEY.match(self.blocked_by)):
            raise ValueError(
                f"BLOCKED must name the EM ticket it waits on, not {self.blocked_by!r}"
            )
        if not blocked and self.blocked_by is not None:
            raise ValueError(f"only a BLOCKED status names a ticket, not {self.value.value}")

    def __str__(self) -> str:
        return f"BLOCKED({self.blocked_by})" if self.blocked_by else self.value.value


@dataclass(frozen=True)
class Lane:
    lane_id: str
    priority: LanePriority
    name: str


@dataclass(frozen=True)
class Foundation:
    """A §4 tooling or data step (F1 to F4, D1 to D8, the vault seal) that unlocks cells."""

    foundation_id: str
    name: str
    status: Status[FoundationStatus]
    ticket: str | None

    def __post_init__(self) -> None:
        if self.ticket is not None and not _JIRA_KEY.match(self.ticket):
            raise ValueError(f"{self.foundation_id}: ticket must be an EM key, not {self.ticket!r}")
        if self.status.value is FoundationStatus.DONE and self.ticket is None:
            raise ValueError(f"{self.foundation_id}: a DONE foundation names the ticket it closed")

    @property
    def is_done(self) -> bool:
        return self.status.value is FoundationStatus.DONE


@dataclass(frozen=True)
class SearchCell:
    """One hypothesis in one lane: one declaration, one verdict, one lesson."""

    cell_id: str
    lane_id: str
    hypothesis: str  # the slug of its config/experiments/<slug>.yaml
    parent: str | None
    requires: tuple[str, ...]  # foundation ids that must be DONE before it can run
    status: Status[CellStatus]
    experiments: tuple[str, ...]
    lesson: str

    def __post_init__(self) -> None:
        if not _CELL_ID.match(self.cell_id):
            raise ValueError(f"a cell id is its lane then a slug (L3-idio-gap): {self.cell_id!r}")
        if not self.cell_id.startswith(f"{self.lane_id}-"):
            raise ValueError(f"{self.cell_id}: its id must start with its lane {self.lane_id}")
        if not _SLUG.match(self.hypothesis):
            raise ValueError(f"{self.cell_id}: hypothesis must be a slug, not {self.hypothesis!r}")
        if "\n" in self.lesson:
            raise ValueError(f"{self.cell_id}: the lesson is one line")
        if self.status.value.is_rejection and not self.lesson.strip():
            raise ValueError(f"{self.cell_id}: a {self.status} cell must record its lesson")
        if self.status.value.is_rejection and not self.experiments:
            raise ValueError(f"{self.cell_id}: a {self.status} verdict names its experiment ids")

    @property
    def is_terminal(self) -> bool:
        return self.status.value.is_terminal


class SearchMap:
    """The whole map, checked as a unit: ids unique, every reference resolving, no parent loops."""

    def __init__(
        self,
        lanes: Sequence[Lane],
        foundations: Sequence[Foundation],
        cells: Sequence[SearchCell],
    ) -> None:
        self._lanes = {lane.lane_id: lane for lane in lanes}
        self._foundations = {f.foundation_id: f for f in foundations}
        self._cells = {cell.cell_id: cell for cell in cells}
        self._check_unique("lane", [lane.lane_id for lane in lanes])
        self._check_unique("foundation", [f.foundation_id for f in foundations])
        self._check_unique("cell", [cell.cell_id for cell in cells])
        for cell in cells:
            self._check_references(cell)
        for cell in cells:
            self._check_acyclic(cell)

    @property
    def lanes(self) -> Mapping[str, Lane]:
        return dict(self._lanes)

    @property
    def foundations(self) -> Mapping[str, Foundation]:
        return dict(self._foundations)

    @property
    def cells(self) -> Mapping[str, SearchCell]:
        return dict(self._cells)

    def children(self, cell_id: str) -> tuple[SearchCell, ...]:
        return tuple(cell for cell in self._cells.values() if cell.parent == cell_id)

    def is_runnable(self, cell: SearchCell) -> bool:
        """A TODO cell whose required foundations are all DONE."""
        return cell.status.value is CellStatus.TODO and all(
            self._foundations[f].is_done for f in cell.requires
        )

    def is_exhausted(self) -> bool:
        return bool(self._cells) and all(cell.is_terminal for cell in self._cells.values())

    def status_counts(self) -> dict[CellStatus, int]:
        counts: dict[CellStatus, int] = dict.fromkeys(CellStatus, 0)
        for cell in self._cells.values():
            counts[cell.status.value] += 1
        return counts

    @staticmethod
    def _check_unique(kind: str, ids: Sequence[str]) -> None:
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate {kind} id(s): {', '.join(duplicates)}")

    def _check_references(self, cell: SearchCell) -> None:
        if cell.lane_id not in self._lanes:
            raise ValueError(f"{cell.cell_id}: unknown lane {cell.lane_id}")
        if cell.parent is not None and cell.parent not in self._cells:
            raise ValueError(f"{cell.cell_id}: unknown parent {cell.parent}")
        unknown = sorted(set(cell.requires) - set(self._foundations))
        if unknown:
            raise ValueError(f"{cell.cell_id}: unknown foundation(s) {', '.join(unknown)}")

    def _check_acyclic(self, cell: SearchCell) -> None:
        seen = {cell.cell_id}
        parent = cell.parent
        while parent is not None:
            if parent in seen:
                raise ValueError(f"{cell.cell_id}: its parent chain loops through {parent}")
            seen.add(parent)
            parent = self._cells[parent].parent


class SearchMapLoader:
    """Reads `search-map.yaml` strictly; every structural error is a `ConfigurationError`."""

    _TOP = frozenset({"lanes", "foundations", "cells"})
    _LANE = frozenset({"id", "priority", "name"})
    _FOUNDATION = frozenset({"id", "name", "status", "ticket"})
    _CELL = frozenset(
        {"id", "lane", "hypothesis", "parent", "requires", "status", "experiments", "lesson"}
    )

    def load(self, path: Path) -> SearchMap:
        document = self._read(path)
        self._check_keys(path, "the map", document, self._TOP, required=self._TOP)
        try:
            return SearchMap(
                [self._lane(path, raw) for raw in self._list(path, document, "lanes")],
                [self._foundation(path, raw) for raw in self._list(path, document, "foundations")],
                [self._cell(path, raw) for raw in self._list(path, document, "cells")],
            )
        except ValueError as error:
            raise ConfigurationError(f"{path}: {error}") from error

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"cannot read search map {path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{path} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{path} must be a mapping of lanes, foundations and cells")
        return document

    @staticmethod
    def _list(path: Path, document: dict[str, Any], key: str) -> list[dict[str, Any]]:
        raw = document[key] or []
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise ConfigurationError(f"{path}: {key} must be a list of mappings")
        return raw

    @staticmethod
    def _check_keys(
        path: Path, where: str, raw: dict[str, Any], allowed: frozenset[str],
        *, required: frozenset[str],
    ) -> None:  # fmt: skip
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ConfigurationError(f"{path}: {where}: unknown key(s) {', '.join(unknown)}")
        missing = sorted(required - set(raw))
        if missing:
            raise ConfigurationError(f"{path}: {where}: missing {', '.join(missing)}")

    def _lane(self, path: Path, raw: dict[str, Any]) -> Lane:
        self._check_keys(path, f"lane {raw.get('id')}", raw, self._LANE, required=self._LANE)
        try:
            priority = LanePriority(str(raw["priority"]))
        except ValueError:
            raise ConfigurationError(f"{path}: lane {raw['id']}: priority must be P1-P3") from None
        return Lane(str(raw["id"]), priority, str(raw["name"]))

    def _foundation(self, path: Path, raw: dict[str, Any]) -> Foundation:
        where = f"foundation {raw.get('id')}"
        self._check_keys(path, where, raw, self._FOUNDATION, required=self._FOUNDATION - {"ticket"})
        ticket = raw.get("ticket")
        return Foundation(
            str(raw["id"]),
            str(raw["name"]),
            self._status(path, where, FoundationStatus, raw["status"]),
            None if ticket is None else str(ticket),
        )

    def _cell(self, path: Path, raw: dict[str, Any]) -> SearchCell:
        where = f"cell {raw.get('id')}"
        self._check_keys(
            path, where, raw, self._CELL, required=frozenset({"id", "lane", "hypothesis", "status"})
        )
        parent = raw.get("parent")
        return SearchCell(
            cell_id=str(raw["id"]),
            lane_id=str(raw["lane"]),
            hypothesis=str(raw["hypothesis"]),
            parent=None if parent is None else str(parent),
            requires=self._strings(path, f"{where}.requires", raw.get("requires")),
            status=self._status(path, where, CellStatus, raw["status"]),
            experiments=self._strings(path, f"{where}.experiments", raw.get("experiments")),
            lesson=str(raw.get("lesson") or ""),
        )

    @staticmethod
    def _strings(path: Path, where: str, raw: object) -> tuple[str, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise ConfigurationError(f"{path}: {where} must be a list")
        return tuple(str(item) for item in raw)

    @staticmethod
    def _status(path: Path, where: str, kind: type[S], raw: object) -> Status[S]:
        text = str(raw)
        blocked = _BLOCKED.match(text)
        try:
            if blocked:
                return Status(kind("BLOCKED"), blocked.group("key"))
            return Status(kind(text))
        except ValueError as error:
            names = ", ".join(member.value for member in kind)
            raise ConfigurationError(
                f"{path}: {where}: status {text!r} is not one of {names} ({error})"
            ) from None
