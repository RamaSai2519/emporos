"""Jev's meta-decision modes applied to a ranked scan (EM-152 / EM-161).

`JevMetaDecisionFilter` sits between `OpportunityScanner` and `PortfolioAllocator`. It never
replaces either: the allocator still enforces every capital/risk constraint on whatever survives
here, and every approved signal still passes through `RiskEngine` before execution. Jev can only
narrow or re-rank the candidate list this pipeline was already going to consider — it cannot
manufacture a candidate, size a position, or bypass a limit.

Every candidate Jev is asked about produces a `JevReview`, kept whether or not the candidate
survived, so the decision is auditable (EM-161: "Jev decisions and metadata are persisted for
auditability") even when the pipeline goes on to reject it. Persistence itself is EM-165's job;
this module only guarantees the record exists to persist.

Modes:

* CONFIRMATION — a binary keep/drop per candidate.
* RANKING — a surviving candidate's `confidence` is scaled by Jev's own confidence and the
  candidate list is re-sorted, so Jev influences ORDER, never eligibility outright (a low-
  confidence confirmation just sinks in the ranking rather than vanishing).
* STRATEGY_SELECTION — the same per-candidate ask as ranking. Jev's role here is choosing which
  STRATEGY to trust when several compete for the same instrument; since `PortfolioAllocator`
  already keeps only the best-ranked candidate per instrument, feeding it Jev-informed scores is
  what makes that choice Jev-aware, without a separate multi-candidate comparison request shape.

A failed request (timeout, malformed reply) or an ABSTAIN is treated identically: pass under
`fail_open`, reject otherwise. `JevConfig.fail_open` defaults to `False`, so live trading is
fail-closed by construction; a backtest wanting to measure Jev-off-vs-on can opt into fail_open
explicitly (EM-162).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from emporos.jev.config import JevConfig
from emporos.jev.models import CONFIRM, CONFIRMATION, JevDecision, JevRequest
from emporos.jev.protocol import JevProvider
from emporos.opportunity.models import OpportunityCandidate


@dataclass(frozen=True)
class JevReview:
    """One candidate Jev was asked about, and what it said. Kept even for a candidate that did
    not survive — a rejection Jev caused is exactly as auditable as one it did not."""

    candidate: OpportunityCandidate
    decision: JevDecision


@dataclass(frozen=True)
class JevFilterResult:
    candidates: tuple[OpportunityCandidate, ...]  # survivors, ready for the allocator
    reviews: tuple[JevReview, ...]  # every candidate Jev was asked about
    rejected: tuple[JevReview, ...]  # subset of `reviews` that did not survive


class JevMetaDecisionFilter:
    def __init__(self, provider: JevProvider, config: JevConfig) -> None:
        self._provider = provider
        self._config = config

    async def apply(self, candidates: tuple[OpportunityCandidate, ...]) -> JevFilterResult:
        if not self._config.enabled:
            return JevFilterResult(candidates=candidates, reviews=(), rejected=())
        if self._config.mode == CONFIRMATION:
            return await self._confirmation(candidates)
        # RANKING and STRATEGY_SELECTION share the same per-candidate confidence-weighting logic;
        # see the module docstring for why that is the honest scope for strategy selection.
        return await self._ranking(candidates)

    async def _confirmation(self, candidates: tuple[OpportunityCandidate, ...]) -> JevFilterResult:
        reviews: list[JevReview] = []
        survivors: list[OpportunityCandidate] = []
        rejected: list[JevReview] = []
        for candidate in candidates:
            review = JevReview(candidate, await self._ask(candidate))
            reviews.append(review)
            if self._confirmed(review.decision):
                survivors.append(candidate)
            else:
                rejected.append(review)
        return JevFilterResult(tuple(survivors), tuple(reviews), tuple(rejected))

    async def _ranking(self, candidates: tuple[OpportunityCandidate, ...]) -> JevFilterResult:
        reviews: list[JevReview] = []
        adjusted: list[OpportunityCandidate] = []
        rejected: list[JevReview] = []
        for candidate in candidates:
            decision = await self._ask(candidate)
            review = JevReview(candidate, decision)
            reviews.append(review)
            if not self._confirmed(decision):
                rejected.append(review)
                continue
            adjusted.append(self._reweighted(candidate, decision))
        ranked = tuple(sorted(adjusted, key=lambda c: c.score, reverse=True))
        return JevFilterResult(ranked, tuple(reviews), tuple(rejected))

    async def _ask(self, candidate: OpportunityCandidate) -> JevDecision:
        return await self._provider.decide(_to_request(candidate))

    def _confirmed(self, decision: JevDecision) -> bool:
        """Whether this decision lets the candidate through. A confirmed decision still needs to
        clear `confidence_threshold` when Jev supplied one; everything else (reject, abstain,
        failure, an unrecognized value already normalized to abstain) falls back to
        `fail_open`."""
        if decision.ok and decision.decision == CONFIRM:
            if decision.confidence is None:
                return True
            return decision.confidence >= Decimal(str(self._config.confidence_threshold))
        return self._config.fail_open

    @staticmethod
    def _reweighted(candidate: OpportunityCandidate, decision: JevDecision) -> OpportunityCandidate:
        if decision.confidence is None:
            return candidate
        return replace(candidate, confidence=candidate.confidence * decision.confidence)


def _to_request(candidate: OpportunityCandidate) -> JevRequest:
    return JevRequest(
        symbol=candidate.instrument_id,
        timeframe=candidate.timeframe.value,
        regime=candidate.regime.value if candidate.regime is not None else None,
        strategy_name=candidate.strategy_name,
        direction=candidate.direction.value,
        entry=candidate.entry.amount,
        stop=candidate.stop.amount,
        target=candidate.target.amount,
        expected_edge=candidate.expected_edge,
        confidence=candidate.confidence,
        as_of=candidate.generated_at,
    )
