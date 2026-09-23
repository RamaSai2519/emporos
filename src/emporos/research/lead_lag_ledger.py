"""The lead-lag trial ledger (EM-180): every intraday predictor/target evaluation attempted,
whether it held up or not — the same append-only, never-overwritten pattern as
`emporos.research.ledger.FeatureTrialLedger`. The record itself (`LeadLagTrial`) is pure domain
(`emporos.domain.lead_lag_trials`), so both this in-memory ledger and
`emporos.persistence.lead_lag_ledger.MongoLeadLagTrialLedger` depend on it without either
depending on the other or on `emporos.research`.
"""

from __future__ import annotations

from typing import Protocol

from emporos.domain.lead_lag_trials import DuplicateLeadLagTrialError, LeadLagTrial

__all__ = [
    "DuplicateLeadLagTrialError",
    "InMemoryLeadLagTrialLedger",
    "LeadLagTrial",
    "LeadLagTrialLedger",
]


class LeadLagTrialLedger(Protocol):
    async def append(self, trial: LeadLagTrial) -> None:
        """Raises `DuplicateLeadLagTrialError` when the id is already there."""
        ...

    async def all(self) -> list[LeadLagTrial]:
        """Every trial, oldest first."""
        ...


class InMemoryLeadLagTrialLedger:
    def __init__(self) -> None:
        self._trials: dict[str, LeadLagTrial] = {}

    async def append(self, trial: LeadLagTrial) -> None:
        if trial.trial_id in self._trials:
            raise DuplicateLeadLagTrialError(trial.trial_id)
        self._trials[trial.trial_id] = trial

    async def all(self) -> list[LeadLagTrial]:
        return sorted(self._trials.values(), key=lambda t: (t.recorded_at, t.trial_id))
