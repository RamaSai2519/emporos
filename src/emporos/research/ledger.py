"""The feature-trial ledger (EM-178): every hypothesis/feature evaluation attempted, whether it
held up or not — the same append-only, never-overwritten pattern as the strategy trial ledger
(`emporos.backtest.robustness.trials.TrialLedger`). The record itself (`FeatureTrial`) is pure
domain (`emporos.domain.feature_trials`), so both this in-memory ledger and
`emporos.persistence.feature_ledger.MongoFeatureTrialLedger` depend on it without either depending
on the other or on `emporos.research`.
"""

from __future__ import annotations

from typing import Protocol

from emporos.domain.feature_trials import DuplicateFeatureTrialError, FeatureTrial

__all__ = [
    "DuplicateFeatureTrialError",
    "FeatureTrial",
    "FeatureTrialLedger",
    "InMemoryFeatureTrialLedger",
]


class FeatureTrialLedger(Protocol):
    async def append(self, trial: FeatureTrial) -> None:
        """Raises `DuplicateFeatureTrialError` when the id is already there; never overwrites."""
        ...

    async def all(self) -> list[FeatureTrial]:
        """Every trial, oldest first."""
        ...


class InMemoryFeatureTrialLedger:
    def __init__(self) -> None:
        self._trials: dict[str, FeatureTrial] = {}

    async def append(self, trial: FeatureTrial) -> None:
        if trial.trial_id in self._trials:
            raise DuplicateFeatureTrialError(trial.trial_id)
        self._trials[trial.trial_id] = trial

    async def all(self) -> list[FeatureTrial]:
        return sorted(self._trials.values(), key=lambda t: (t.recorded_at, t.trial_id))
