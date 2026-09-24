"""Looks taken before the research ledgers lived in Mongo (EM-191 F2B, EDGE_SEARCH_PLAN.md §3.2).

EM-178..EM-181 ran their grids with in-memory ledgers, so the Mongo ledgers are empty and program-
wide N undercounted them. `docs/research/edge-search/historical-trials.yaml` lists each published
proof run and the trials its report says it recorded; `HistoricalGridCounter` counts one family of
them as one source of looks. Nothing is written to a ledger: no metric is invented for a trial
whose result is only known in aggregate, and the count cannot drift by being run twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from emporos.core.errors import ConfigurationError

__all__ = ["GridFamily", "HistoricalGrid", "HistoricalGridCounter", "HistoricalGridLoader"]

_KEYS = frozenset({"id", "family", "ticket", "trials", "source", "evidence"})


class GridFamily(StrEnum):
    FEATURE = "feature"
    CROSS_SECTIONAL = "cross_sectional"
    LEAD_LAG = "lead_lag"


@dataclass(frozen=True)
class HistoricalGrid:
    """One published proof run: how many looks it took, and the report line that says so."""

    grid_id: str
    family: GridFamily
    ticket: str
    trials: int
    source: Path
    evidence: str


class HistoricalGridLoader:
    """Reads the manifest as strictly as a declaration: unknown keys, bad counts and duplicate ids
    are refused, so a typo cannot quietly lower N."""

    def load(self, path: Path) -> tuple[HistoricalGrid, ...]:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ConfigurationError(f"cannot read {path}: {error}") from error
        rows = document.get("grids") if isinstance(document, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ConfigurationError(f"{path}: expected a non-empty 'grids' list")
        grids = tuple(self._grid(path, row) for row in rows)
        ids = [g.grid_id for g in grids]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ConfigurationError(f"{path}: duplicate grid ids: {', '.join(duplicates)}")
        return grids

    @staticmethod
    def _grid(path: Path, row: Any) -> HistoricalGrid:
        if not isinstance(row, dict) or set(row) != _KEYS:
            raise ConfigurationError(f"{path}: each grid needs exactly {sorted(_KEYS)}: {row!r}")
        trials = row["trials"]
        if isinstance(trials, bool) or not isinstance(trials, int) or trials <= 0:
            raise ConfigurationError(f"{path}: {row['id']}: trials must be a positive integer")
        try:
            family = GridFamily(row["family"])
        except ValueError as error:
            raise ConfigurationError(
                f"{path}: {row['id']}: unknown family {row['family']!r}"
            ) from error
        return HistoricalGrid(
            grid_id=str(row["id"]),
            family=family,
            ticket=str(row["ticket"]),
            trials=trials,
            source=Path(str(row["source"])),
            evidence=str(row["evidence"]),
        )


class HistoricalGridCounter:
    """One family's documented proof runs, as a source of looks for `ProgramTrialCount`."""

    def __init__(self, name: str, family: GridFamily, grids: Sequence[HistoricalGrid]) -> None:
        self._name = name
        self._trials = sum(g.trials for g in grids if g.family is family)

    @property
    def name(self) -> str:
        return self._name

    async def count(self) -> int:
        return self._trials
