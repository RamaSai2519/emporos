"""Every screen is a look, and every look is counted (EM-191 F3, EDGE_SEARCH_PLAN.md §3.2).

The screener is cheap, so it is the easiest place to fish. A screen therefore appends one line to
an append-only ledger, win or lose, and program-wide N (`ProgramTrialCount`) counts the lines. The
default ledger is a committed JSONL file: auditable in git, no load on the shared Atlas cluster.

A screen is identified by what it looked at (hypothesis, parameters, universe, period, declared
size). Running the same screen again is the same look, not a new one, and is not counted twice.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from emporos.core.errors import ConfigurationError
from emporos.research.screen_evaluator import ScreenResult

__all__ = [
    "InMemoryScreenLedger", "JsonlScreenLedger", "ScreenIdentity", "ScreenLedger",
    "ScreenLedgerCounter", "ScreenRecord",
]  # fmt: skip


@dataclass(frozen=True)
class ScreenIdentity:
    hypothesis: str  # the slug of its config/experiments/<slug>.yaml
    parameters: Mapping[str, str]
    universe: str
    first_day: date
    last_day: date
    position_value: Decimal

    @property
    def screen_id(self) -> str:
        canonical = json.dumps(
            [
                self.hypothesis, sorted(self.parameters.items()), self.universe,
                self.first_day.isoformat(), self.last_day.isoformat(), str(self.position_value),
            ],
            separators=(",", ":"),
        )  # fmt: skip
        return f"SCR-{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class ScreenRecord:
    identity: ScreenIdentity
    result: ScreenResult
    recorded_at: datetime

    def as_document(self) -> dict[str, object]:
        i, r = self.identity, self.result
        return {
            "screen_id": i.screen_id,
            "hypothesis": i.hypothesis,
            "parameters": dict(i.parameters),
            "universe": i.universe,
            "first_day": i.first_day.isoformat(),
            "last_day": i.last_day.isoformat(),
            "position_value": str(i.position_value),
            "trades": r.trades,
            "net_mean": None if r.net_mean is None else str(r.net_mean),
            "net_t": None if r.net_t is None else str(r.net_t),
            "passed": r.passed,
            "failed_checks": list(r.failed_checks),
            "advisory": r.advisory,
            "recorded_at": self.recorded_at.isoformat(),
        }


class ScreenLedger(Protocol):
    def record(self, record: ScreenRecord) -> bool:
        """Append the screen. False, and nothing written, when this exact screen is already in."""
        ...

    def count(self) -> int: ...


class InMemoryScreenLedger:
    def __init__(self) -> None:
        self._records: dict[str, ScreenRecord] = {}

    def record(self, record: ScreenRecord) -> bool:
        screen_id = record.identity.screen_id
        if screen_id in self._records:
            return False
        self._records[screen_id] = record
        return True

    def count(self) -> int:
        return len(self._records)


class JsonlScreenLedger:
    """One JSON object per line, appended and never rewritten."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: set[str] | None = None

    def record(self, record: ScreenRecord) -> bool:
        seen = self._ids()
        screen_id = record.identity.screen_id
        if screen_id in seen:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_document(), sort_keys=True) + "\n")
        seen.add(screen_id)
        return True

    def count(self) -> int:
        return len(self._ids())

    def _ids(self) -> set[str]:
        if self._seen is None:
            self._seen = self._read()
        return self._seen

    def _read(self) -> set[str]:
        if not self._path.exists():
            return set()
        ids: set[str] = set()
        for number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ids.add(str(json.loads(line)["screen_id"]))
            except (ValueError, KeyError, TypeError) as error:
                raise ConfigurationError(
                    f"{self._path}:{number}: not a screen record: {error}"
                ) from error
        return ids


class ScreenLedgerCounter:
    """The screen ledger as a source of looks for `ProgramTrialCount`."""

    def __init__(self, ledger: ScreenLedger, name: str = "screens") -> None:
        self._ledger = ledger
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def count(self) -> int:
        return self._ledger.count()
