"""EM-162: Jev-on vs Jev-off, compared through actual fills and the realistic cost model.

`emporos.opportunity.jev_experiment.JevOnOffExperiment` answers this at the DECISION layer —
cheaply, with no broker — comparing what each side's `OpportunityPipeline` would have done.
`JevOnOffBacktestExperiment` is the other half: it runs the SAME `MultiStrategyBacktestSpec` —
identical strategies, universe, capital, costs, slippage, and portfolio constraints — through two
independently-built `MultiStrategyBacktestEngine`s, one with Jev disabled and one with it
enabled, and reports what changed once real fills and charges are applied. Each side's own
`MetricsReport` already carries net P&L, drawdown, risk-adjusted returns, trade count, turnover,
and regime/strategy attribution (`by_regime`, `by_strategy`); `JevPnlComparison` adds only the
convenience deltas a reader would otherwise have to compute by hand. Jev's own API cost and
latency come from `MultiStrategyBacktestResult.jev` (`JevRunSummary`, accumulated by the engine
over the whole run) — the treatment side's alone, since the baseline never calls Jev.

Lives in `emporos.backtest`, not `emporos.opportunity`: it constructs backtest runs, and
opportunity selection must never depend on any one runtime that consumes it — the same reason
`OpportunityRunner` and `MultiStrategyBacktestEngine` itself live outside that package.
"""

from __future__ import annotations

from dataclasses import dataclass

from emporos.backtest.multi_engine import (
    MultiStrategyBacktestEngine,
    MultiStrategyBacktestResult,
    MultiStrategyBacktestSpec,
)
from emporos.domain.money import Money


@dataclass(frozen=True)
class JevPnlComparison:
    baseline: MultiStrategyBacktestResult  # Jev disabled
    treatment: MultiStrategyBacktestResult  # Jev enabled

    @property
    def net_pnl_delta(self) -> Money:
        """Treatment minus baseline ending equity: negative means Jev cost money, on net, against
        whatever it saved by rejecting losing candidates."""
        return self.treatment.metrics.ending_equity - self.baseline.metrics.ending_equity

    @property
    def trade_count_delta(self) -> int:
        return self.treatment.metrics.trades.count - self.baseline.metrics.trades.count


class JevOnOffBacktestExperiment:
    """`baseline` and `treatment` must be independently constructed `MultiStrategyBacktestEngine`s
    built from identical readers/registries/tick sizes/schedules/allocators, differing only in
    their `jev_filter` — mirrors `JevOnOffExperiment`'s own baseline/treatment split at the
    decision layer, for the same reason: only the caller knows what "identical" means for a given
    run, and building both is not this class's job."""

    def __init__(
        self, baseline: MultiStrategyBacktestEngine, treatment: MultiStrategyBacktestEngine
    ) -> None:
        self._baseline = baseline
        self._treatment = treatment

    async def run(self, spec: MultiStrategyBacktestSpec) -> JevPnlComparison:
        baseline_result = await self._baseline.run(spec)
        treatment_result = await self._treatment.run(spec)
        return JevPnlComparison(baseline_result, treatment_result)
