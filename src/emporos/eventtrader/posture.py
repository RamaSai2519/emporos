"""The daily posture (PROFIT_PLAN §12.3): one call a session before the open, from the previous
evening's headlines and the market's state, becomes a scale on every risk budget.

hold is 0 (no new entry), normal 0.6, aggressive 1.0. It only ever scales a budget down from its
declared size: it never overrides a limit. A day whose call fails or returns an invalid reply is
HOLD, as an invalid reply anywhere in the pipeline is no trade. Posture calls are decided up front
like the events' (they depend on nothing the book does), and their token cost is kept per day so
the replay charges it to the day it was spent for."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import IST
from emporos.eventtrader.events import HEADLINE, ContextBuilder, EventStore, MarketEvent
from emporos.eventtrader.llm.guards import ScopedTallies
from emporos.eventtrader.llm.pricing import PriceTable
from emporos.eventtrader.stages.base import LlmStage
from emporos.eventtrader.stages.models import Posture, PostureResult
from emporos.eventtrader.stages.stages import PostureInput

__all__ = [
    "POSTURE_SCALES",
    "PostureInputs",
    "PosturePlanner",
    "PostureSchedule",
    "StorePostureInputs",
]

POSTURE_SCALES = {
    Posture.HOLD: Decimal(0),
    Posture.NORMAL: Decimal("0.6"),
    Posture.AGGRESSIVE: Decimal(1),
}
POSTURE_TIME = time(9, 0)
MAX_HEADLINES = 40
HEADLINE_CHARS = 200


class PostureInputs(Protocol):
    def for_day(self, day: date) -> PostureInput:
        """What the posture call sees before `day`'s open."""
        ...


class PostureSchedule:
    """The scale per session; a `PostureSource` for the replay."""

    def __init__(
        self,
        scales: Mapping[date, Decimal],
        cost_by_day: Mapping[date, Decimal],
        failures: Mapping[date, str],
        postures: Mapping[date, Posture],
    ) -> None:
        self._scales, self._cost, self._failures = dict(scales), dict(cost_by_day), dict(failures)
        self._postures = dict(postures)

    def scale_for(self, day: date) -> Decimal:
        """A day with no plan is HOLD: a day the schedule was not made for has no permission."""
        return self._scales.get(day, POSTURE_SCALES[Posture.HOLD])

    @property
    def cost_by_day(self) -> dict[date, Decimal]:
        return dict(self._cost)

    @property
    def total_cost_inr(self) -> Decimal:
        return sum(self._cost.values(), Decimal(0))

    @property
    def failures(self) -> dict[date, str]:
        return dict(self._failures)

    def counts(self) -> Counter[str]:
        return Counter(p.value for p in self._postures.values())


class PosturePlanner:
    def __init__(
        self,
        stage: LlmStage[PostureInput, PostureResult],
        inputs: PostureInputs,
        scopes: ScopedTallies,
        prices: PriceTable,
        concurrency: int = 8,
    ) -> None:
        self._stage, self._inputs, self._scopes, self._prices = stage, inputs, scopes, prices
        self._limit = asyncio.Semaphore(concurrency)

    async def plan(self, sessions: Sequence[date]) -> PostureSchedule:
        scales: dict[date, Decimal] = {}
        cost: dict[date, Decimal] = {}
        failures: dict[date, str] = {}
        postures: dict[date, Posture] = {}

        async def one(day: date) -> None:
            item = self._inputs.for_day(day)
            key = f"posture:{day.isoformat()}"
            async with self._limit:
                with self._scopes.scope(key):
                    outcome = await self._stage.run(item, item.decision_at)
            cost[day] = self._scopes.tally(key).cost_inr(self._prices)
            posture = outcome.value.posture if outcome.value is not None else Posture.HOLD
            if outcome.value is None:
                failures[day] = outcome.error or "unknown"
            postures[day], scales[day] = posture, POSTURE_SCALES[posture]

        try:
            async with asyncio.TaskGroup() as group:
                for day in sessions:
                    group.create_task(one(day))
        except ExceptionGroup as failed:
            raise failed.exceptions[0] from None
        return PostureSchedule(scales, cost, failures, postures)


class StorePostureInputs:
    """The previous evening's headlines (everything published since the previous session's close,
    up to 09:00) and the market lines as of 09:00, taken from the context builder with a
    market-wide item so no name's numbers are among them."""

    def __init__(
        self,
        store: EventStore,
        context: ContextBuilder,
        previous_session: Mapping[date, date],
    ) -> None:
        self._store, self._context, self._previous = store, context, dict(previous_session)

    def for_day(self, day: date) -> PostureInput:
        at = datetime.combine(day, POSTURE_TIME, tzinfo=IST)
        previous = self._previous.get(day, day - timedelta(days=1))
        since = datetime.combine(previous, time(15, 30), tzinfo=IST)
        events = [e for e in self._store.events_between(since, at) if e.usable_from <= at]
        headlines = [_line(e) for e in events][-MAX_HEADLINES:]
        market = MarketEvent(
            f"posture:{day.isoformat()}", "", "NIFTY", at, at, HEADLINE, "", "", ""
        )
        state = dict(self._context.context(market, at).lines)
        return PostureInput(headlines, state, at)


def _line(event: MarketEvent) -> str:
    text = f"{event.symbol}: {event.subject or event.category}"
    return text[:HEADLINE_CHARS]
