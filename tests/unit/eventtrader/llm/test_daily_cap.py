"""The daily token cap: counts by UTC day, pauses past it, resumes after midnight, never repeats."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest

from emporos.core.clock import FixedClock
from emporos.eventtrader.llm.client import LlmReply, LlmRequest
from emporos.eventtrader.llm.daily_cap import DailyTokenCap, TokenCappedClient, usage_by_day
from emporos.eventtrader.llm.journal import InMemoryJournal, JournaledClient, Recording
from tests.support.fakes import AdvancingSleeper

MODEL = "gpt-4o-mini-2024-07-18"
NOON = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
TODAY, TOMORROW = date(2026, 9, 26), date(2026, 9, 27)


def request(user: str = "u") -> LlmRequest:
    return LlmRequest("triage", MODEL, "v", "h", "s", user, 100, NOON)


class Model:
    def __init__(self, tokens_in: int = 400, tokens_out: int = 100) -> None:
        self.calls, self._in, self._out = 0, tokens_in, tokens_out

    async def complete(self, r: LlmRequest) -> LlmReply:
        self.calls += 1
        return LlmReply("{}", self._in, self._out, MODEL)


def cap(limit: int, clock: FixedClock, used: dict[date, int] | None = None) -> DailyTokenCap:
    messages: list[str] = []
    c = DailyTokenCap(limit, used or {}, clock, AdvancingSleeper(clock), messages.append)
    c.messages = messages  # type: ignore[attr-defined]
    return c


async def test_calls_within_the_day_are_counted_by_their_real_tokens() -> None:
    clock = FixedClock(NOON)
    c = cap(10_000, clock)
    client = TokenCappedClient(Model(), c)

    await client.complete(request())
    await client.complete(request())

    assert c.usage() == {TODAY: 1000} and c.pauses == 0


async def test_a_call_that_could_pass_the_cap_waits_for_midnight_utc_and_is_then_made() -> None:
    clock = FixedClock(NOON)
    c = cap(1000, clock, {TODAY: 900})  # 100 left: one call's worst case does not fit
    model = Model()

    await TokenCappedClient(model, c).complete(request())

    assert c.pauses == 1 and model.calls == 1
    assert clock.now() > datetime(2026, 9, 27, 0, 0, tzinfo=UTC)  # it woke after midnight
    assert c.usage() == {TODAY: 900, TOMORROW: 500}
    assert "daily token cap reached on 2026-09-26" in c.messages[0]  # type: ignore[attr-defined]


async def test_calls_in_flight_are_reserved_so_they_cannot_carry_the_day_past_the_cap() -> None:
    clock = FixedClock(NOON)
    worst = len("s") + len("u") + 100
    c = cap(worst * 2 + 1, clock)  # room for two in flight

    class Slow(Model):
        async def complete(self, r: LlmRequest) -> LlmReply:
            await asyncio.sleep(0.01)
            return await super().complete(r)

    slow = Slow(10, 10)
    await asyncio.gather(*[TokenCappedClient(slow, c).complete(request()) for _ in range(3)])

    assert c.pauses >= 1 and slow.calls == 3
    assert all(v <= c.cap for v in c.usage().values())


async def test_a_failed_call_gives_its_reservation_back() -> None:
    class Down:
        async def complete(self, r: LlmRequest) -> LlmReply:
            raise RuntimeError("down")

    clock = FixedClock(NOON)
    c = cap(1000, clock)
    with pytest.raises(RuntimeError):
        await TokenCappedClient(Down(), c).complete(request())
    await TokenCappedClient(Model(), c).complete(request())

    assert c.pauses == 0 and c.usage() == {TODAY: 500}


async def test_a_recorded_answer_costs_nothing_and_never_waits() -> None:
    clock = FixedClock(NOON)
    c = cap(1000, clock, {TODAY: 1000})  # the day is spent
    journal = InMemoryJournal()
    model = Model()
    stack = JournaledClient(TokenCappedClient(model, c), journal, record=True, clock=clock)
    journal.append(Recording(request().request_hash, "t", MODEL, "v", "h", "a", "{}", 5, 5, MODEL))

    await stack.complete(request())

    assert model.calls == 0 and c.pauses == 0


def test_a_call_larger_than_a_whole_day_is_an_error_not_an_endless_wait() -> None:
    clock = FixedClock(NOON)
    with pytest.raises(ValueError, match="larger than a whole day"):
        asyncio.run(cap(10, clock).reserve(11))
    with pytest.raises(ValueError, match="at least one"):
        DailyTokenCap(0, {}, clock, AdvancingSleeper(clock))


def test_the_journal_says_what_was_used_each_utc_day_for_one_model() -> None:
    def rec(h: str, when: str, model: str = MODEL) -> Recording:
        return Recording(h, "t", model, "v", "p", "a", "{}", 700, 300, model, when)

    journal = InMemoryJournal()
    journal.append(rec("a", "2026-09-26T23:59:00+00:00"))
    journal.append(rec("b", "2026-09-27T05:29:00+05:30"))  # 05:29 IST is 23:59 on the 26th UTC
    journal.append(rec("c", "2026-09-27T00:01:00+00:00"))
    journal.append(rec("d", "", MODEL))  # no time: not counted
    journal.append(rec("e", "2026-09-26T10:00:00+00:00", "gpt-4o-2024-08-06"))

    assert usage_by_day(journal.recordings(), MODEL) == {TODAY: 2000, TOMORROW: 1000}
