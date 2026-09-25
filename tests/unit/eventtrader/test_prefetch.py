"""Deciding events concurrently, with each event charged for its own calls."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.eventtrader.llm.client import LlmReply, LlmRequest
from emporos.eventtrader.llm.guards import ScopedTallies, TallyingClient, TokenTally
from emporos.eventtrader.llm.http_clients import GATEWAY_MODEL
from emporos.eventtrader.llm.pricing import ModelPrice, PriceTable
from emporos.eventtrader.pipeline import PipelineDecision, Verdict
from emporos.eventtrader.prefetch import DecisionPrefetcher
from emporos.eventtrader.stages.stages import EventInput
from tests.unit.eventtrader.fakes import NOON, item

D = Decimal
PRICES = PriceTable(
    {GATEWAY_MODEL: ModelPrice(D("1000000"), D("0"))}, D("1")
)  # Re 1 per input token


class TokenClient:
    """Each request costs as many input tokens as its user text says; slower for earlier events."""

    async def complete(self, request: LlmRequest) -> LlmReply:
        await asyncio.sleep(0.002 if request.user == "3" else 0)
        return LlmReply("{}", int(request.user), 0, GATEWAY_MODEL)


class TwoCalls:
    def __init__(self, client: TallyingClient) -> None:
        self._client = client

    async def decide(self, item_: EventInput) -> PipelineDecision:
        n = item_.event.text  # the test puts the token count in the event text
        if n == "boom":
            raise RuntimeError("boom")
        for stage in ("triage", "judge"):
            request = LlmRequest(stage, GATEWAY_MODEL, "v1", "h", "s", n, 10, NOON)
            await self._client.complete(request)
        return PipelineDecision(item_.event.event_id, Verdict.NOT_MATERIAL)


def items(*tokens: str) -> list[EventInput]:
    return [
        item(event_id=f"E{i}", text=t, usable_from=NOON + timedelta(minutes=i))
        for i, t in enumerate(tokens)
    ]


def prefetcher(scopes: ScopedTallies, tally: TokenTally, **kw: int) -> DecisionPrefetcher:
    client = TallyingClient(TokenClient(), tally, scopes)
    return DecisionPrefetcher(TwoCalls(client), scopes, PRICES, **kw)


async def test_each_event_is_charged_for_its_own_calls_whatever_the_finishing_order() -> None:
    scopes, tally = ScopedTallies(), TokenTally()

    got = await prefetcher(scopes, tally, concurrency=8).run(items("3", "1", "2"))

    consumed = []
    for it in items("3", "1", "2"):
        before = got.total_inr()
        await got.decide(it)
        consumed.append(got.total_inr() - before)
    assert consumed == [D(6), D(2), D(4)]  # two calls of n tokens each
    assert got.total_cost_inr() == D(12) and len(got) == 3
    assert sum(tally.tokens_in.values()) == 12  # the run's own tally saw every call too


async def test_the_answers_come_back_in_event_order_and_can_be_listed() -> None:
    got = await prefetcher(ScopedTallies(), TokenTally()).run(items("3", "1"))

    assert list(got.decisions()) == ["E0", "E1"]


async def test_an_event_that_was_never_prefetched_is_an_error() -> None:
    got = await prefetcher(ScopedTallies(), TokenTally()).run(items("1"))

    with pytest.raises(KeyError, match="not prefetched"):
        await got.decide(item(event_id="OTHER"))


async def test_the_first_failure_propagates_instead_of_a_partial_result() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        await prefetcher(ScopedTallies(), TokenTally()).run(items("1", "boom", "2"))


def test_concurrency_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        prefetcher(ScopedTallies(), TokenTally(), concurrency=0)


def test_calls_made_outside_any_scope_are_not_attributed() -> None:
    scopes = ScopedTallies()
    request = LlmRequest("triage", GATEWAY_MODEL, "v1", "h", "s", "5", 10, NOON)

    scopes.add(request, LlmReply("{}", 5, 0, GATEWAY_MODEL))

    assert scopes.tally("anything").tokens_in == {}
