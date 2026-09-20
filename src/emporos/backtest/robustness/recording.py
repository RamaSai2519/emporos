"""Turning finished walk-forward runs into ledger entries, so no attempt goes unrecorded.

A walk-forward run is a search: every candidate is tried on every training window before one is
chosen, and each of those tries is a trial whether or not it was chosen. `WalkForwardTrials` reads
a `WalkForwardResult` and lists every one of them; `LedgerRecorder` appends them. Recording is a
separate step after the run, so it cannot influence which candidate is chosen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.backtest.robustness.trials import TrialLedger
from emporos.backtest.tuning import SHARPE
from emporos.backtest.walkforward_run import WalkForwardResult, WindowOutcome
from emporos.domain.experiments import Trial, TrialRole


@dataclass(frozen=True)
class TrialContext:
    experiment: str  # the research programme, e.g. "curation-2026-09"
    batch: str  # unique per execution, so repeating an experiment adds trials instead of colliding
    dataset_version: str
    cost_model: str
    recorded_at: datetime


class ResultRecorder(Protocol):
    async def record(self, strategy: str, result: WalkForwardResult) -> None: ...


class NoRecording:
    async def record(self, strategy: str, result: WalkForwardResult) -> None:
        return None


class WalkForwardTrials:
    """Every backtest a walk-forward run performed, as trials."""

    def __init__(
        self,
        context: TrialContext,
        annualisation_days: int = 252,
        dataset_stamp: Callable[[], str] | None = None,
    ) -> None:
        """`dataset_stamp` names the exact bars the runs read (e.g. the candle cache's
        fingerprint); it is read when trials are listed, after the runs have finished."""
        self._context = context
        self._stamp = dataset_stamp
        self._root_days = DecimalMath.sqrt(Decimal(annualisation_days))

    def of(self, strategy: str, result: WalkForwardResult) -> list[Trial]:
        trials: list[Trial] = []
        for index, outcome in enumerate(result.outcomes):
            trials.extend(self._training(strategy, index, result.objective, outcome))
            trials.append(self._test(strategy, index, outcome))
        return trials

    def _training(
        self, strategy: str, index: int, objective: str, outcome: WindowOutcome
    ) -> list[Trial]:
        return [
            self._trial(
                strategy,
                index,
                TrialRole.TRAIN,
                score.candidate.name,
                daily_sharpe=self._daily(objective, score.score),
                note=f"training window {outcome.window.train.start.date()}"
                f"..{outcome.window.train.end.date()}; {objective} score {score.score}",
            )  # fmt: skip
            for score in outcome.training
        ]

    def _test(self, strategy: str, index: int, outcome: WindowOutcome) -> Trial:
        test, window = outcome.test, outcome.window.test
        return self._trial(
            strategy, index, TrialRole.TEST, outcome.chosen.name,
            run_id=test.run_id, config_hash=test.config_hash,
            trade_count=test.metrics.trades.count, net_pnl=test.metrics.trades.net_pnl.amount,
            daily_sharpe=self._daily(SHARPE.name, test.metrics.returns.sharpe),
            note=f"test window {window.start.date()}..{window.end.date()}",
        )  # fmt: skip

    def _trial(
        self, strategy: str, index: int, role: TrialRole, candidate: str, *,
        run_id: str | None = None, config_hash: str | None = None,
        trade_count: int | None = None, net_pnl: Decimal | None = None,
        daily_sharpe: Decimal | None = None, note: str = "",
    ) -> Trial:  # fmt: skip
        c = self._context
        dataset = (
            c.dataset_version if self._stamp is None else f"{c.dataset_version}; {self._stamp()}"
        )
        return Trial(
            trial_id=f"{c.experiment}/{c.batch}/{strategy}/w{index}/{role.value}/{candidate}",
            experiment=c.experiment, strategy=strategy, candidate=candidate, role=role,
            dataset_version=dataset, config_hash=config_hash, cost_model=c.cost_model,
            recorded_at=c.recorded_at, run_id=run_id, trade_count=trade_count, net_pnl=net_pnl,
            daily_sharpe=daily_sharpe, note=note,
        )  # fmt: skip

    def _daily(self, objective: str, score: Decimal | None) -> Decimal | None:
        """A Sharpe objective is annualised; the ledger keeps it per day. Others carry no Sharpe."""
        if objective != SHARPE.name or score is None:
            return None
        return DecimalMath.divide(score, self._root_days)


class LedgerRecorder:
    def __init__(self, ledger: TrialLedger, trials: WalkForwardTrials) -> None:
        self._ledger = ledger
        self._trials = trials

    async def record(self, strategy: str, result: WalkForwardResult) -> None:
        for trial in self._trials.of(strategy, result):
            await self._ledger.append(trial)
