"""The blackout days a regime filter avoids, from a committed YAML file (EM-230).

Every row names its kind and the public page it was read on, so a date with no source cannot enter
the file unnoticed. The loader refuses an unknown kind, a missing source, a bad date or a repeat."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

__all__ = ["KINDS", "BlackoutFileError", "load_blackout_days"]

KINDS = frozenset({"union_budget", "election_result"})


class BlackoutFileError(ValueError):
    """The blackout file is malformed."""


def load_blackout_days(path: Path) -> frozenset[date]:
    try:
        document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = document["events"]
    except (OSError, KeyError, yaml.YAMLError) as error:
        raise BlackoutFileError(f"{path}: {error!r}") from error
    days: set[date] = set()
    for row in rows:
        try:
            day = date.fromisoformat(str(row["date"]))
            kind, source = str(row["kind"]), str(row["source"]).strip()
        except (KeyError, TypeError, ValueError) as error:
            raise BlackoutFileError(f"{path}: bad row {row!r}: {error!r}") from error
        if kind not in KINDS:
            raise BlackoutFileError(f"{path}: {day}: unknown kind {kind!r}")
        if not source:
            raise BlackoutFileError(f"{path}: {day}: no source")
        if day in days:
            raise BlackoutFileError(f"{path}: {day} listed twice")
        days.add(day)
    if not days:
        raise BlackoutFileError(f"{path}: no events")
    return frozenset(days)
