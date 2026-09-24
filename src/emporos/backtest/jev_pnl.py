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

"Identical" is a fact, not a convention (EM-187): `JevOnOffBacktestExperiment.from_factory` builds
both arms from one `EngineFactory`, so they can differ only in `jev_filter`, and every run is
stamped with an `ExperimentFingerprint` of the spec, data provenance, fee schedule and holdout
that both arms are verified against; a mismatch raises rather than producing a comparison.

Lives in `emporos.backtest`, not `emporos.opportunity`: it constructs backtest runs, and
opportunity selection must never depend on any one runtime that consumes it — the same reason
`OpportunityRunner` and `MultiStrategyBacktestEngine` itself live outside that package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from emporos.backtest.fingerprint import (
    ExperimentFingerprint,
    ExperimentFingerprinter,
    FingerprintContext,
    FingerprintMismatch,
)
from emporos.backtest.multi_engine import (
    MultiStrategyBacktestResult,
    MultiStrategyBacktestSpec,
)
from emporos.domain.money import Money
from emporos.opportunity.jev_filter import JevMetaDecisionFilter


class BacktestRunner(Protocol):
    """What one arm needs: run a spec, return its result. `MultiStrategyBacktestEngine` is one."""

    async def run(self, spec: MultiStrategyBacktestSpec) -> MultiStrategyBacktestResult: ...


class EngineFactory(Protocol):
    """Builds an arm from one set of collaborators. The only thing that varies between arms is
    the Jev filter (None for the baseline), so nothing else can differ."""

    def build(self, jev_filter: JevMetaDecisionFilter | None) -> BacktestRunner: ...


@dataclass(frozen=True)
class JevPnlComparison:
    baseline: MultiStrategyBacktestResult  # Jev disabled
    treatment: MultiStrategyBacktestResult  # Jev enabled
    fingerprint: ExperimentFingerprint  # of what both arms verifiably had in common

    @property
    def net_pnl_delta(self) -> Money:
        """Treatment minus baseline ending equity: negative means Jev cost money, on net, against
        whatever it saved by rejecting losing candidates."""
        return self.treatment.metrics.ending_equity - self.baseline.metrics.ending_equity

    @property
    def trade_count_delta(self) -> int:
        return self.treatment.metrics.trades.count - self.baseline.metrics.trades.count


class ArmVerification:
    """Checks that two finished arms really ran under the assumptions the fingerprint names."""

    def check(
        self,
        fingerprint: ExperimentFingerprint,
        context: FingerprintContext,
        baseline: MultiStrategyBacktestResult,
        treatment: MultiStrategyBacktestResult,
    ) -> None:
        for arm in (baseline, treatment):
            fingerprint.verify(arm.spec, context)
        if baseline.strategies != treatment.strategies or baseline.risk_gate != treatment.risk_gate:
            raise FingerprintMismatch(
                "the two arms ran different strategies or a different risk gate"
            )


class JevOnOffBacktestExperiment:
    """`baseline` and `treatment` are the two arms. Prefer `from_factory`, which builds both from
    one `EngineFactory` so they can only differ in `jev_filter`; the plain constructor remains for
    a caller who has built two engines itself, and the fingerprint check still applies."""

    def __init__(
        self,
        baseline: BacktestRunner,
        treatment: BacktestRunner,
        fingerprinter: ExperimentFingerprinter | None = None,
    ) -> None:
        self._baseline = baseline
        self._treatment = treatment
        self._fingerprinter = fingerprinter or ExperimentFingerprinter()

    @classmethod
    def from_factory(
        cls,
        factory: EngineFactory,
        treatment_filter: JevMetaDecisionFilter,
        fingerprinter: ExperimentFingerprinter | None = None,
    ) -> JevOnOffBacktestExperiment:
        return cls(factory.build(None), factory.build(treatment_filter), fingerprinter)

    async def run(
        self, spec: MultiStrategyBacktestSpec, context: FingerprintContext | None = None
    ) -> JevPnlComparison:
        context = context or FingerprintContext()
        fingerprint = self._fingerprinter.fingerprint(spec, context)
        baseline = await self._baseline.run(spec)
        treatment = await self._treatment.run(spec)
        ArmVerification().check(fingerprint, context, baseline, treatment)
        return JevPnlComparison(baseline, treatment, fingerprint)
