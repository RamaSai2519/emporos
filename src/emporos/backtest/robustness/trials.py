"""The trial ledger: an append-only record of every experiment attempted, failures included.

Nothing can rewrite it. `TrialLedger` has `append` and read methods and no others, so what was
tried, and how often, cannot be quietly edited after a result is seen. The in-memory ledger here
is the reference implementation; Mongo's lives in `emporos.persistence.trial_ledger`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.domain.experiments import DuplicateTrialError, Trial


class TrialLedger(Protocol):
    async def append(self, trial: Trial) -> None:
        """Raises `DuplicateTrialError` when the id is already there; never overwrites."""
        ...

    async def all(self) -> list[Trial]:
        """Every trial, oldest first."""
        ...


class InMemoryTrialLedger:
    def __init__(self) -> None:
        self._trials: dict[str, Trial] = {}

    async def append(self, trial: Trial) -> None:
        if trial.trial_id in self._trials:
            raise DuplicateTrialError(trial.trial_id)
        self._trials[trial.trial_id] = trial

    async def all(self) -> list[Trial]:
        return sorted(self._trials.values(), key=lambda t: (t.recorded_at, t.trial_id))


@dataclass(frozen=True)
class TrialStatistics:
    """What the multiple-testing correction needs to know about the search."""

    count: int  # every trial, scored or not: this is how many times we looked
    scored: int  # those that carry a Sharpe ratio
    sharpe_variance: Decimal | None  # sample variance of their daily Sharpes; None below two

    @classmethod
    def of(cls, trials: Sequence[Trial]) -> TrialStatistics:
        sharpes = [t.daily_sharpe for t in trials if t.daily_sharpe is not None]
        variance = DecimalMath.sample_variance(sharpes) if len(sharpes) >= 2 else None
        return cls(len(trials), len(sharpes), variance)
