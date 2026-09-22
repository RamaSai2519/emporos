"""Persisting one opportunity-selection tick's full decision trail (EM-152 / EM-165).

`OpportunityAuditLog` turns a `BarOutcome` (EM-158's pipeline result) into an
`OpportunityScanRecord` and writes it — the same shape `MongoRejectionLog` uses for risk
rejections (`emporos.risk.rejection_log`): a narrow `Store` Protocol the composition root wires
to a real `Repository`, an `IdGenerator` for the document id, nothing else. Every strategy
evaluated, every candidate ranked, every scan- and Jev-level rejection with its reason, and what
was ultimately sized — one document per tick, so a live result can always be checked against
what the pipeline actually saw and decided.

Like `DeploymentGate`, this is opt-in: `OpportunityRunner` only writes an audit record when a
log is injected, so a backtest that runs thousands of ticks isn't forced to pay for persistence
it doesn't want.
"""

from __future__ import annotations

from typing import Any, Protocol

from emporos.core.ids import IdGenerator
from emporos.opportunity.allocator import Allocation
from emporos.opportunity.jev_filter import JevReview
from emporos.opportunity.models import OpportunityCandidate
from emporos.opportunity.pipeline import BarOutcome
from emporos.persistence.records import OpportunityScanRecord


class OpportunityScanStore(Protocol):
    async def insert(self, record: OpportunityScanRecord) -> None: ...


class OpportunityAuditLog:
    def __init__(self, store: OpportunityScanStore, ids: IdGenerator) -> None:
        self._store = store
        self._ids = ids

    async def record(self, outcome: BarOutcome) -> None:
        scan = outcome.scan
        await self._store.insert(
            OpportunityScanRecord(
                _id=self._ids.new_ulid(),
                ts=outcome.ts,
                instrument_ids=sorted(
                    {c.instrument_id for c in scan.candidates}
                    | {r.instrument_id for r in scan.rejected}
                ),
                regimes={
                    c.instrument_id: c.regime.value if c.regime is not None else ""
                    for c in scan.candidates
                },
                candidates=[_candidate_doc(c) for c in scan.candidates],
                rejected=[
                    {
                        "strategy_name": r.strategy_name,
                        "instrument_id": r.instrument_id,
                        "reason": r.reason,
                    }
                    for r in scan.rejected
                ],
                jev_reviews=[_jev_doc(review) for review in outcome.jev.reviews],
                jev_rejected=[_jev_doc(review) for review in outcome.jev.rejected],
                allocations=[_allocation_doc(a) for a in outcome.allocations],
            )
        )


def _candidate_doc(candidate: OpportunityCandidate) -> dict[str, Any]:
    return {
        "strategy_name": candidate.strategy_name,
        "instrument_id": candidate.instrument_id,
        "timeframe": candidate.timeframe.value,
        "direction": candidate.direction.value,
        "entry": str(candidate.entry.amount),
        "stop": str(candidate.stop.amount),
        "target": str(candidate.target.amount),
        "expected_edge": str(candidate.expected_edge),
        "confidence": str(candidate.confidence),
        "score": str(candidate.score),
        "regime": candidate.regime.value if candidate.regime is not None else None,
    }


def _jev_doc(review: JevReview) -> dict[str, Any]:
    decision = review.decision
    return {
        "strategy_name": review.candidate.strategy_name,
        "instrument_id": review.candidate.instrument_id,
        "decision": decision.decision,
        "confidence": str(decision.confidence) if decision.confidence is not None else None,
        "provider": decision.provider,
        "model": decision.model,
        "latency_ms": decision.latency_ms,
        "tokens_used": decision.tokens_used,
        "error": decision.error,
    }


def _allocation_doc(allocation: Allocation) -> dict[str, Any]:
    return {
        "strategy_name": allocation.candidate.strategy_name,
        "instrument_id": allocation.candidate.instrument_id,
        "quantity": allocation.quantity,
        "committed_capital": str(allocation.committed_capital.amount),
        "committed_risk": str(allocation.committed_risk.amount),
    }
