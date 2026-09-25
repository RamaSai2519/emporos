"""Deciding every event up front, concurrently, then replaying in order (EM-240).

A decision depends only on the event and the context as of its decision time, never on the book, so
the calls can run many at a time (bounded, to stay inside the gateway's limits) and the replay can
then walk the events one by one with the answers in hand. Token cost is kept per event, so the
replay charges an event with what ITS calls cost, whatever order they finished in."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.eventtrader.llm.guards import ScopedTallies
from emporos.eventtrader.llm.pricing import PriceTable
from emporos.eventtrader.pipeline import PipelineDecision
from emporos.eventtrader.replay.engine import Decider
from emporos.eventtrader.stages.stages import EventInput

__all__ = ["DecisionPrefetcher", "PrefetchedDecisions"]


@dataclass(frozen=True)
class _Answer:
    decision: PipelineDecision
    cost_inr: Decimal


class PrefetchedDecisions:
    """Serves the answers as a `Decider`, and the running token cost as a `TokenMeter`: the meter
    advances by an event's cost when its decision is consumed, which is what the replay's per-event
    attribution reads."""

    def __init__(self, answers: dict[str, _Answer]) -> None:
        self._answers = answers
        self._consumed = Decimal(0)

    def __len__(self) -> int:
        return len(self._answers)

    def decisions(self) -> dict[str, PipelineDecision]:
        return {k: a.decision for k, a in self._answers.items()}

    def total_cost_inr(self) -> Decimal:
        """Every call made in the prefetch, consumed by the replay or not."""
        return sum((a.cost_inr for a in self._answers.values()), Decimal(0))

    async def decide(self, item: EventInput) -> PipelineDecision:
        answer = self._answers.get(item.event.event_id)
        if answer is None:
            raise KeyError(f"event {item.event.event_id} was not prefetched")
        self._consumed += answer.cost_inr
        return answer.decision

    def total_inr(self) -> Decimal:
        return self._consumed


class DecisionPrefetcher:
    def __init__(
        self, decider: Decider, scopes: ScopedTallies, prices: PriceTable, concurrency: int = 8
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency is at least 1")
        self._decider, self._scopes, self._prices = decider, scopes, prices
        self._limit = asyncio.Semaphore(concurrency)

    async def run(self, items: Sequence[EventInput]) -> PrefetchedDecisions:
        """Decide every item. The first failure (a budget refusal, a missing recording) cancels the
        rest and propagates: a run that cannot finish must not report partial results."""
        answers: dict[str, _Answer] = {}
        try:
            async with asyncio.TaskGroup() as group:
                for item in items:
                    group.create_task(self._one(item, answers))
        except ExceptionGroup as failed:
            raise failed.exceptions[0] from None  # the caller handles the cause, not the wrapper
        # the replay walks events in their own order, whatever order the calls finished in
        return PrefetchedDecisions({i.event.event_id: answers[i.event.event_id] for i in items})

    async def _one(self, item: EventInput, answers: dict[str, _Answer]) -> None:
        key = item.event.event_id
        async with self._limit:
            with self._scopes.scope(key):
                decision = await self._decider.decide(item)
        cost = self._scopes.tally(key).cost_inr(self._prices)
        answers[key] = _Answer(decision, cost)
