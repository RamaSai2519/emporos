"""Where the opportunity pipeline's decisions become real orders (EM-152 / EM-163 / EM-164).

`OpportunityRunner` drives `OpportunityPipeline.on_bars` and sends everything it approves —
every exit, and every allocator-approved entry — through `GatedExecutionSink`
(`emporos.session.signal_path`), the same account-wide record → risk → execution route manual
orders and session square-off already use. Nothing here is paper- or live-specific: the sink is
injected, so a paper-backed sink and an Angel-One-backed sink produce IDENTICAL decisions from
identical bars — the same "no separate simplified implementation" guarantee EM-163 asks for is
true by construction, not by parallel maintenance.

Known limitation, not fixed here: a fill is not routed back to the strategy that caused it
(`Strategy.on_order_update` is not called for orders this runner places). Every builtin strategy
this repository ships derives its state from `ctx.positions`/`ctx.history` each bar rather than
from fill callbacks, so this is not a live bug today — but a strategy that DID rely on
`on_order_update` would need this runner extended with per-instrument ownership tracking first
(the same gap flagged on EM-158 for the backtest engine).

An optional `deployment_gate` (`DeploymentGate`, EM-164) scales or drops entries by the causing
strategy's deployment stage before they reach the sink — conservative sizing for a strategy still
proving itself live, none at all for one that has not reached live yet. Left unset (the default)
for paper/backtest roots that want to see a strategy's true, unthrottled behavior; a live
composition root supplies one.

An optional `audit_log` (`OpportunityAuditLog`, EM-165) persists the FULL pipeline outcome —
every candidate, rejection, Jev review and allocation the tick produced — before the deployment
gate or execution ever touch it, so an audit record always reflects what the pipeline actually
decided, not what a downstream policy let through.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from emporos.domain.candles import Candle
from emporos.domain.signals import Signal
from emporos.opportunity.audit import OpportunityAuditLog
from emporos.opportunity.deployment_gate import DeploymentGate
from emporos.opportunity.pipeline import BarOutcome, OpportunityPipeline
from emporos.session.signal_path import Submission


class GatedSignalSink(Protocol):
    """What `GatedExecutionSink` (`emporos.session.signal_path`) offers — named locally, like
    `control.handlers.ManualSignalPort`, so this package depends on the shape it needs rather
    than reaching into `emporos.session` for a Protocol that belongs to a different layer."""

    async def submit_as(self, signal: Signal, signal_id: str | None = None) -> Submission: ...


@dataclass(frozen=True)
class RunOutcome:
    pipeline: BarOutcome
    submissions: tuple[Submission, ...]

    @property
    def placed(self) -> tuple[Submission, ...]:
        return tuple(submission for submission in self.submissions if submission.placed)


class OpportunityRunner:
    def __init__(
        self,
        pipeline: OpportunityPipeline,
        sink: GatedSignalSink,
        deployment_gate: DeploymentGate | None = None,
        audit_log: OpportunityAuditLog | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._sink = sink
        self._deployment_gate = deployment_gate
        self._audit_log = audit_log

    async def on_bars(self, candles: Sequence[Candle]) -> RunOutcome:
        outcome = await self._pipeline.on_bars(candles)
        if self._audit_log is not None:
            await self._audit_log.record(outcome)
        allocations = outcome.allocations
        if self._deployment_gate is not None:
            allocations = self._deployment_gate.apply(allocations)
        entries = tuple(allocation.signal for allocation in allocations)
        submissions: list[Submission] = []
        # Sequential and total-ordered, like every other signal path in this project: an exit
        # settles before the next entry is even reviewed.
        for signal in (*outcome.exits, *entries):
            submissions.append(await self._sink.submit_as(signal))
        return RunOutcome(pipeline=outcome, submissions=tuple(submissions))
