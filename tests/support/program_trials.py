"""A program-wide trial count for tests that need the Deflated Sharpe priced at a known N."""

from __future__ import annotations

from emporos.backtest.robustness.program_trials import ProgramTrialCount
from emporos.backtest.robustness.trials import InMemoryTrialLedger


class FixedTrialCounter:
    def __init__(self, name: str, count: int) -> None:
        self._name = name
        self._count = count

    @property
    def name(self) -> str:
        return self._name

    async def count(self) -> int:
        return self._count


def program_trials(prior: int = 35) -> ProgramTrialCount:
    """An empty strategy ledger plus `prior` unscored looks from elsewhere in the program."""
    return ProgramTrialCount(
        {"strategy trials": InMemoryTrialLedger()}, [FixedTrialCounter("prior", prior)]
    )
