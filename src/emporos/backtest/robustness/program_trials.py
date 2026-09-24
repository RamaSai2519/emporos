"""Program-wide trial count N (EM-191 F2, EDGE_SEARCH_PLAN.md §3.2).

The Deflated Sharpe prices the luck of the WHOLE search, so N must be every look the program has
taken: every strategy trial, every feature / cross-sectional / lead-lag trial, every published
experiment report and (from F3) every screener evaluation. `ProgramTrialCount` is the only thing
that assembles those into the `TrialStatistics` a curation's Deflated Sharpe is priced at;
`RobustnessAssessor` takes one by type, so a caller cannot hand it a local count instead.

The sources overlap (a published curation report and its ledger trials describe the same run), and
the sum counts both. That can only raise N, and so the hurdle: an N that is too high costs a real
edge some confidence; an N that is too low manufactures one.

Only trials that carry a daily Sharpe (the strategy trials) inform the spread of Sharpes; every
source adds to the count.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.backtest.robustness.trials import TrialLedger, TrialStatistics
from emporos.core.errors import ConfigurationError

__all__ = [
    "LedgerTrialCounter", "ProgramTrialCount", "ProgramTrials", "RegistryIndexCounter",
    "StoreTrialCounter", "TrialCounter",
]  # fmt: skip


class TrialCounter(Protocol):
    """One source of looks: how many trials it holds, under a name the breakdown reports."""

    @property
    def name(self) -> str: ...

    async def count(self) -> int: ...


class _Listing(Protocol):
    async def all(self) -> Sequence[object]: ...


class _Counting(Protocol):
    async def count(self) -> int: ...


class LedgerTrialCounter:
    """Counts any append-only ledger by listing it (the in-memory ledgers, a run's own trials)."""

    def __init__(self, name: str, ledger: _Listing) -> None:
        self._name = name
        self._ledger = ledger

    @property
    def name(self) -> str:
        return self._name

    async def count(self) -> int:
        return len(await self._ledger.all())


class StoreTrialCounter:
    """Counts a ledger that can count itself without loading its trials (the Mongo ledgers)."""

    def __init__(self, name: str, store: _Counting) -> None:
        self._name = name
        self._store = store

    @property
    def name(self) -> str:
        return self._name

    async def count(self) -> int:
        return await self._store.count()


class RegistryIndexCounter:
    """The published experiment reports (EM-188): one row per experiment in `index.json`."""

    def __init__(self, index: Path, name: str = "experiment registry") -> None:
        self._index = index
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def count(self) -> int:
        if not self._index.exists():
            return 0
        try:
            document = json.loads(self._index.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ConfigurationError(
                f"cannot read the registry index {self._index}: {error}"
            ) from error
        rows = document.get("experiments") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise ConfigurationError(f"{self._index}: expected an 'experiments' list")
        return len(rows)


@dataclass(frozen=True)
class ProgramTrials:
    """N and where it came from: the statistics the DSR is priced at, plus the per-source counts."""

    statistics: TrialStatistics
    by_source: Mapping[str, int]

    @property
    def count(self) -> int:
        return self.statistics.count


class ProgramTrialCount:
    """Assembles program-wide N. `scored` ledgers carry Sharpes and count; `counters` only count."""

    def __init__(
        self,
        scored: Mapping[str, TrialLedger],
        counters: Sequence[TrialCounter] = (),
    ) -> None:
        if not scored:
            raise ValueError("program-wide N needs at least the strategy trial ledger")
        names = [*scored, *(c.name for c in counters)]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"each trial source is counted once: {', '.join(duplicates)}")
        self._scored = dict(scored)
        self._counters = tuple(counters)

    async def trials(self) -> ProgramTrials:
        by_source: dict[str, int] = {}
        sharpes = []
        for name, ledger in self._scored.items():
            trials = await ledger.all()
            by_source[name] = len(trials)
            sharpes += [t.daily_sharpe for t in trials if t.daily_sharpe is not None]
        for counter in self._counters:
            by_source[counter.name] = await counter.count()
        variance = DecimalMath.sample_variance(sharpes) if len(sharpes) >= 2 else None
        statistics = TrialStatistics(sum(by_source.values()), len(sharpes), variance)
        return ProgramTrials(statistics, by_source)

    async def statistics(self) -> TrialStatistics:
        return (await self.trials()).statistics
