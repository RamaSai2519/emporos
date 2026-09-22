"""Jev-on vs Jev-off comparison at the decision layer (EM-152 / EM-162).

`JevOnOffExperiment` drives one identical bar sequence through two `OpportunityPipeline`
instances built from the same strategies, universe, capital and constraints — one with Jev
enabled, one without — and reports what changed in the DECISIONS each made: how many candidates
the scan ranked, how many Jev reviewed and rejected, and Jev's own latency/token cost. This is
the honest, currently-reachable half of EM-162: it needs no broker, no fills, no cost model, and
works today.

What this does NOT report: net P&L, drawdown, Sharpe/Sortino, turnover, or regime/strategy P&L
attribution. Those need each side run through actual fills under the realistic cost model —
`emporos.backtest.jev_pnl.JevOnOffBacktestExperiment` is that other half, built on top of
`MultiStrategyBacktestEngine` (EM-158). It lives in `emporos.backtest`, not here: opportunity
selection must never depend on any one runtime that consumes it. `JevRunSummary` below is the
seam between the two — the same per-bar Jev cost this module reports, in a shape a caller
driving `OpportunityPipeline` directly (rather than through this module's own batch loop) can
accumulate over a whole run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from emporos.domain.candles import Candle
from emporos.opportunity.pipeline import BarOutcome, OpportunityPipeline


@dataclass(frozen=True)
class SideOutcome:
    """One pipeline side's summary for one bar-batch."""

    ts: datetime
    candidates_scanned: int
    candidates_allocated: int
    jev_reviews: int
    jev_rejections: int
    jev_latency_ms: int
    jev_tokens: int

    @classmethod
    def from_bar_outcome(cls, ts: datetime, outcome: BarOutcome) -> SideOutcome:
        return cls(
            ts=ts,
            candidates_scanned=len(outcome.scan.candidates),
            candidates_allocated=len(outcome.allocations),
            jev_reviews=len(outcome.jev.reviews),
            jev_rejections=len(outcome.jev.rejected),
            jev_latency_ms=sum(review.decision.latency_ms for review in outcome.jev.reviews),
            jev_tokens=sum(review.decision.tokens_used or 0 for review in outcome.jev.reviews),
        )


@dataclass(frozen=True)
class JevRunSummary:
    """Jev's own cost accumulated over a run: how many candidates it reviewed, how many it
    rejected, and what that cost in latency and tokens. `add` returns a new summary rather than
    mutating in place, so a caller folding this over a run of bar-batches (a `reduce`, not a
    loop with a mutable accumulator) never needs to reason about shared state."""

    reviews: int = 0
    rejections: int = 0
    latency_ms: int = 0
    tokens: int = 0

    def add(self, outcome: BarOutcome) -> JevRunSummary:
        return JevRunSummary(
            reviews=self.reviews + len(outcome.jev.reviews),
            rejections=self.rejections + len(outcome.jev.rejected),
            latency_ms=self.latency_ms + sum(r.decision.latency_ms for r in outcome.jev.reviews),
            tokens=self.tokens + sum(r.decision.tokens_used or 0 for r in outcome.jev.reviews),
        )


@dataclass(frozen=True)
class DecisionComparisonReport:
    baseline: tuple[SideOutcome, ...]  # Jev disabled
    treatment: tuple[SideOutcome, ...]  # Jev enabled

    @property
    def allocation_count_delta(self) -> int:
        """Treatment minus baseline: negative means Jev made the pipeline more conservative."""
        return self._total(self.treatment) - self._total(self.baseline)

    @property
    def total_jev_latency_ms(self) -> int:
        return sum(side.jev_latency_ms for side in self.treatment)

    @property
    def total_jev_tokens(self) -> int:
        return sum(side.jev_tokens for side in self.treatment)

    @property
    def total_jev_rejections(self) -> int:
        return sum(side.jev_rejections for side in self.treatment)

    @staticmethod
    def _total(sides: tuple[SideOutcome, ...]) -> int:
        return sum(side.candidates_allocated for side in sides)


class JevOnOffExperiment:
    """`baseline` and `treatment` must be independently-built `OpportunityPipeline`s over
    identical strategies/universe/constraints, differing only in their Jev filter — the caller
    owns building both, since only the caller knows what "identical strategies" means for a
    given run (separate `Strategy` instances so one side's state never leaks into the other's)."""

    def __init__(self, baseline: OpportunityPipeline, treatment: OpportunityPipeline) -> None:
        self._baseline = baseline
        self._treatment = treatment

    async def run(self, batches: Sequence[Sequence[Candle]]) -> DecisionComparisonReport:
        baseline_outcomes: list[SideOutcome] = []
        treatment_outcomes: list[SideOutcome] = []
        for batch in batches:
            if not batch:
                raise ValueError("a bar batch cannot be empty")
            ts = batch[0].ts
            baseline_outcomes.append(
                SideOutcome.from_bar_outcome(ts, await self._baseline.on_bars(batch))
            )
            treatment_outcomes.append(
                SideOutcome.from_bar_outcome(ts, await self._treatment.on_bars(batch))
            )
        return DecisionComparisonReport(tuple(baseline_outcomes), tuple(treatment_outcomes))
