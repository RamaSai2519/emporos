"""The daily posture: scales, the HOLD default for a failed call, cost per day, the inputs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from emporos.eventtrader.events import MarketContext, MarketEvent
from emporos.eventtrader.llm.client import LlmReply, LlmRequest
from emporos.eventtrader.llm.guards import ScopedTallies
from emporos.eventtrader.llm.http_clients import GATEWAY_MODEL
from emporos.eventtrader.llm.pricing import ModelPrice, PriceTable
from emporos.eventtrader.posture import (
    POSTURE_SCALES,
    PosturePlanner,
    PostureSchedule,
    StorePostureInputs,
)
from emporos.eventtrader.stages.base import StageOutcome
from emporos.eventtrader.stages.models import Posture, PostureResult
from emporos.eventtrader.stages.stages import PostureInput
from tests.unit.eventtrader.fakes import event
from tests.unit.eventtrader.replay.fakes import MON, THU, TUE, WED, at

D = Decimal
PRICES = PriceTable({GATEWAY_MODEL: ModelPrice(D("1000000"), D(0))}, D(1))


class Inputs:
    def for_day(self, day: date) -> PostureInput:
        return PostureInput([], {}, at(day, 9))


class ScriptedStage:
    name = "posture"

    def __init__(self, scopes: ScopedTallies, answers: Mapping[date, Posture | None]) -> None:
        self._scopes, self._answers = scopes, answers

    async def run(self, item: PostureInput, as_of: datetime) -> StageOutcome[PostureResult]:
        day = as_of.date()
        request = LlmRequest("posture", GATEWAY_MODEL, "v1", "h", "s", "u", 10, as_of)
        self._scopes.add(request, LlmReply("{}", day.day, 0, GATEWAY_MODEL))
        answer = self._answers[day]
        if answer is None:
            return StageOutcome(None, "invalid_reply: no", 2)
        return StageOutcome(PostureResult(answer, "r"), None, 1)


async def test_each_session_gets_the_scale_of_its_posture_and_a_failure_is_hold() -> None:
    scopes = ScopedTallies()
    answers = {MON: Posture.AGGRESSIVE, TUE: Posture.NORMAL, WED: Posture.HOLD, THU: None}
    planner = PosturePlanner(ScriptedStage(scopes, answers), Inputs(), scopes, PRICES)

    schedule = await planner.plan([MON, TUE, WED, THU])

    assert [schedule.scale_for(d) for d in (MON, TUE, WED, THU)] == [D(1), D("0.6"), D(0), D(0)]
    assert set(schedule.failures) == {THU}
    assert schedule.counts() == {"aggressive": 1, "normal": 1, "hold": 2}


async def test_the_cost_is_kept_for_the_day_it_was_spent_for() -> None:
    scopes = ScopedTallies()
    answers = {MON: Posture.NORMAL, TUE: Posture.NORMAL}
    schedule = await PosturePlanner(ScriptedStage(scopes, answers), Inputs(), scopes, PRICES).plan(
        [MON, TUE]
    )

    assert schedule.cost_by_day == {MON: D(4), TUE: D(5)}  # a token a day-of-month
    assert schedule.total_cost_inr == D(9)


def test_a_day_the_schedule_was_not_made_for_is_hold() -> None:
    schedule = PostureSchedule({}, {}, {}, {})

    assert schedule.scale_for(date(2030, 1, 1)) == POSTURE_SCALES[Posture.HOLD]


class Store:
    def __init__(self, events: list[MarketEvent]) -> None:
        self._events = events
        self.asked: list[tuple[datetime, datetime]] = []

    def events_between(self, start: datetime, end: datetime) -> list[MarketEvent]:
        self.asked.append((start, end))
        return [e for e in self._events if start <= e.published_at < end]

    def for_symbol(self, symbol: str, start: datetime, end: datetime) -> list[MarketEvent]:
        return []

    def by_id(self, event_id: str) -> MarketEvent | None:
        return None


class MarketLines:
    def __init__(self) -> None:
        self.seen: list[MarketEvent] = []

    def context(self, e: MarketEvent, decision_at: datetime) -> MarketContext:
        self.seen.append(e)
        return MarketContext({"nifty_ret_5d_pct": 1.5})


def test_the_inputs_are_the_evenings_headlines_and_the_market_lines_at_nine() -> None:
    def ev(eid: str, when: datetime) -> MarketEvent:
        return event(event_id=eid, published_at=when, usable_from=when, subject=f"S-{eid}")

    events = [
        ev("early", at(MON, 15, 0).astimezone(UTC)),  # before Monday's close: not the evening
        ev("evening", at(MON, 18, 0).astimezone(UTC)),
        ev("morning", at(TUE, 8, 30).astimezone(UTC)),
        ev("after", at(TUE, 9, 1).astimezone(UTC)),  # after the posture call: never seen
    ]
    lines, store = MarketLines(), Store(events)

    item = StorePostureInputs(store, lines, {TUE: MON}).for_day(TUE)

    assert item.headlines == ["RELIANCE: S-evening", "RELIANCE: S-morning"]
    assert dict(item.state) == {"nifty_ret_5d_pct": 1.5} and item.decision_at == at(TUE, 9)
    assert lines.seen[0].instrument_id == "" and lines.seen[0].usable_from == at(TUE, 9)
    assert store.asked == [(at(MON, 15, 30), at(TUE, 9))]


def test_without_a_known_previous_session_the_evening_starts_the_day_before() -> None:
    store = Store([])
    StorePostureInputs(store, MarketLines(), {}).for_day(TUE)

    assert store.asked[0][0] == at(TUE, 15, 30) - timedelta(days=1)
