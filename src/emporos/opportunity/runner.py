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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from emporos.domain.candles import Candle
from emporos.domain.signals import Signal
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
    def __init__(self, pipeline: OpportunityPipeline, sink: GatedSignalSink) -> None:
        self._pipeline = pipeline
        self._sink = sink

    async def on_bars(self, candles: Sequence[Candle]) -> RunOutcome:
        outcome = await self._pipeline.on_bars(candles)
        submissions: list[Submission] = []
        # Sequential and total-ordered, like every other signal path in this project: an exit
        # settles before the next entry is even reviewed.
        for signal in (*outcome.exits, *outcome.approved_signals):
            submissions.append(await self._sink.submit_as(signal))
        return RunOutcome(pipeline=outcome, submissions=tuple(submissions))
