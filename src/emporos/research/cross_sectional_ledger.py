"""The cross-sectional trial ledger (EM-179): every residual-momentum tail evaluation attempted,
whether it held up or not — the same append-only, never-overwritten pattern as
`emporos.research.ledger.FeatureTrialLedger`. The record itself (`CrossSectionalTrial`) is pure
domain (`emporos.domain.cross_sectional_trials`), so both this in-memory ledger and
`emporos.persistence.cross_sectional_ledger.MongoCrossSectionalTrialLedger` depend on it without
either depending on the other or on `emporos.research`.
"""

from __future__ import annotations

from typing import Protocol

from emporos.domain.cross_sectional_trials import (
    CrossSectionalTrial,
    DuplicateCrossSectionalTrialError,
)

__all__ = [
    "CrossSectionalTrial",
    "CrossSectionalTrialLedger",
    "DuplicateCrossSectionalTrialError",
    "InMemoryCrossSectionalTrialLedger",
]


class CrossSectionalTrialLedger(Protocol):
    async def append(self, trial: CrossSectionalTrial) -> None:
        """Raises `DuplicateCrossSectionalTrialError` when the id is already there."""
        ...

    async def all(self) -> list[CrossSectionalTrial]:
        """Every trial, oldest first."""
        ...


class InMemoryCrossSectionalTrialLedger:
    def __init__(self) -> None:
        self._trials: dict[str, CrossSectionalTrial] = {}

    async def append(self, trial: CrossSectionalTrial) -> None:
        if trial.trial_id in self._trials:
            raise DuplicateCrossSectionalTrialError(trial.trial_id)
        self._trials[trial.trial_id] = trial

    async def all(self) -> list[CrossSectionalTrial]:
        return sorted(self._trials.values(), key=lambda t: (t.recorded_at, t.trial_id))
