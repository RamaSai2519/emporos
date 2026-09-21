"""From a finished walk-forward run to a classified strategy.

`RobustnessAssessor` gathers the evidence, every piece of it from OUT-OF-SAMPLE trades and days
only, runs each analysis, and hands the lot to the verdict policy. It reads the run and, for the two
checks that need one (neighbouring parameters, the always-long baseline), backtests further on the
test windows; it can never change what was chosen or what the run found.

The trial count for the Deflated Sharpe comes from the whole ledger, every experiment ever
recorded, not just this strategy's: the luck to be beaten is the luck of the whole search.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from emporos.backtest.batch import (
    Backtester,
    BatchBacktester,
    BatchItem,
    BatchResults,
    ProgressSink,
    SerialBatch,
    ignore_progress,
)
from emporos.backtest.engine import BacktestSpec
from emporos.backtest.metrics.decimal_math import ZERO
from emporos.backtest.robustness.benchmark import BenchmarkConfig
from emporos.backtest.robustness.concentration import ConcentrationCheck, ConcentrationReport
from emporos.backtest.robustness.cost_sensitivity import CostSensitivity, ScenarioOutcome
from emporos.backtest.robustness.deflated_sharpe import DeflatedSharpe, DeflatedSharpeReport
from emporos.backtest.robustness.monte_carlo import MonteCarlo, MonteCarloConfig, MonteCarloReport
from emporos.backtest.robustness.perturbation import PerturbationReport, PerturbationRunner
from emporos.backtest.robustness.trials import TrialStatistics
from emporos.backtest.robustness.verdict import Evidence, VerdictPolicy, VerdictReport
from emporos.backtest.tuning import ParameterCandidate
from emporos.backtest.walkforward_run import WalkForwardResult
from emporos.core.clock import IST
from emporos.domain.instruments import InstrumentResolver
from emporos.strategies.config import ResolvedStrategyConfig

TrialStatisticsSource = Callable[[], Awaitable[TrialStatistics]]


class AssessorFactory(Protocol):
    """Builds the assessor for one strategy's run, from the engine that ran it."""

    def __call__(
        self, backtester: Backtester, resolver: InstrumentResolver
    ) -> RobustnessAssessor: ...


class HoldBaseline:
    """The always-long baseline: the same universe, sizing, risk rules and charges as the strategy,
    on the strategy's own test windows, with no view at all."""

    def __init__(
        self,
        backtester: Backtester,
        config: ResolvedStrategyConfig,
        batch: BatchBacktester | None = None,
    ) -> None:
        self._batch = batch or SerialBatch(backtester)
        self._config = config

    async def net_pnl(
        self,
        base: BacktestSpec,
        result: WalkForwardResult,
        progress: ProgressSink = ignore_progress,
    ) -> Decimal:
        config = self._config.model_copy(update={"universe": base.config.universe})
        items = [
            BatchItem(
                f"{base.config.name} w{index} always-long baseline",
                replace(base, config=config, window=outcome.window.test),
            )
            for index, outcome in enumerate(result.outcomes)
        ]
        runs = BatchResults(await self._batch.run_many(items, progress)).results()
        return sum((run.metrics.trades.net_pnl.amount for run in runs), ZERO)


@dataclass(frozen=True)
class RobustnessReport:
    strategy: str
    verdict: VerdictReport
    evidence: Evidence
    monte_carlo: MonteCarloReport
    deflated_sharpe: DeflatedSharpeReport
    concentration: ConcentrationReport
    costs: tuple[ScenarioOutcome, ...]
    perturbation: PerturbationReport | None
    baseline_net_pnl: Decimal | None


class RobustnessAssessor:
    def __init__(
        self,
        benchmark: BenchmarkConfig,
        trial_statistics: TrialStatisticsSource,
        perturbation: PerturbationRunner | None = None,
        baseline: HoldBaseline | None = None,
        policy: VerdictPolicy | None = None,
    ) -> None:
        self._benchmark = benchmark
        self._trial_statistics = trial_statistics
        self._perturbation = perturbation
        self._baseline = baseline
        self._policy = policy or VerdictPolicy.standard(benchmark.verdict)

    async def assess(
        self,
        strategy: str,
        result: WalkForwardResult,
        base: BacktestSpec,
        candidates: Sequence[ParameterCandidate],
        progress: ProgressSink = ignore_progress,
    ) -> RobustnessReport:
        thresholds = self._benchmark.verdict
        trades = tuple(t for o in result.outcomes for t in o.test.trades)
        returns = tuple(r for o in result.outcomes for r in o.test.metrics.daily_returns)
        costs = tuple(CostSensitivity().evaluate(trades, self._benchmark.cost_scenarios))
        monte_carlo = MonteCarlo(self._monte_carlo_config()).run(trades)
        deflated = DeflatedSharpe().evaluate(returns, await self._trial_statistics())
        concentration = ConcentrationCheck(thresholds.concentration.top_trades).measure(trades)
        perturbation = await self._perturb(base, result, candidates, progress)
        baseline = (
            None if self._baseline is None else await self._baseline.net_pnl(base, result, progress)
        )
        evidence = Evidence(
            trade_count=len(trades),
            net_pnl=sum((t.net_pnl.amount for t in trades), ZERO),
            adverse_net_pnl=self._adverse(costs),
            history_days=self._weekdays(base),
            window_nets=tuple(o.test.metrics.trades.net_pnl.amount for o in result.outcomes),
            worst_window_drawdown=max(
                (o.test.metrics.drawdown.max_drawdown for o in result.outcomes), default=ZERO
            ),
            monte_carlo=monte_carlo,
            deflated_sharpe=deflated,
            concentration=concentration,
            perturbation=perturbation,
            baseline_net_pnl=baseline,
        )
        return RobustnessReport(
            strategy, self._policy.classify(evidence), evidence, monte_carlo, deflated,
            concentration, costs, perturbation, baseline,
        )  # fmt: skip

    def _monte_carlo_config(self) -> MonteCarloConfig:
        t = self._benchmark.verdict
        return MonteCarloConfig(
            seed=t.monte_carlo.seed,
            resamples=t.monte_carlo.resamples,
            confidence=t.confidence,
            starting_equity=self._benchmark.capital,
            drawdown_limit=t.max_window_drawdown,
            min_trades=t.monte_carlo.min_trades,
        )

    def _adverse(self, costs: Sequence[ScenarioOutcome]) -> Decimal:
        name = self._benchmark.adverse_scenario
        return next(c.net_pnl for c in costs if c.name == name)

    async def _perturb(
        self,
        base: BacktestSpec,
        result: WalkForwardResult,
        candidates: Sequence[ParameterCandidate],
        progress: ProgressSink,
    ) -> PerturbationReport | None:
        if self._perturbation is None:
            return None
        return await self._perturbation.evaluate(base, result.outcomes, candidates, progress)

    @staticmethod
    def _weekdays(base: BacktestSpec) -> int:
        """Weekdays (IST) in the data window: an upper bound on trading days."""
        day, end, count = (
            base.window.start.astimezone(IST).date(),
            base.window.end.astimezone(IST).date(),
            0,
        )
        while day < end:
            count += 1 if day.weekday() < 5 else 0
            day += timedelta(days=1)
        return count
