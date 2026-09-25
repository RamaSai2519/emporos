"""EM-240: the decorators around an LLM call: date guard, USD ceiling, record/replay, tally, and the
OpenAI-compatible HTTP client."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from emporos.eventtrader.llm.budget import (
    BudgetedClient,
    BudgetRefused,
    UsdBudget,
    journal_spend_usd,
    worst_case_usd,
)
from emporos.eventtrader.llm.client import LlmReply, LlmRequest
from emporos.eventtrader.llm.guards import CutoffGuardedClient, TallyingClient, TokenTally
from emporos.eventtrader.llm.http_clients import (
    GATEWAY_MODEL,
    OPENAI_MODEL,
    LlmTransportError,
    OpenAiCompatibleClient,
)
from emporos.eventtrader.llm.journal import (
    InMemoryJournal,
    JournaledClient,
    JsonlJournal,
    NotRecorded,
    Recording,
)
from emporos.eventtrader.llm.pricing import ModelPrice, PriceTable
from emporos.jev.config import JevConfig
from emporos.jev.leakage import JevLeakageError, KnowledgeCutoffGuard
from tests.support.fakes import AdvancingSleeper

AS_OF = datetime(2024, 6, 3, 6, 0, tzinfo=UTC)
PRICES = PriceTable(
    {
        OPENAI_MODEL: ModelPrice(Decimal("2.50"), Decimal("10.00")),
        GATEWAY_MODEL: ModelPrice(Decimal("0.15"), Decimal("0.60")),
    },
    Decimal("88"),
)


def request(
    model: str = OPENAI_MODEL, user: str = '{"a":1}', as_of: datetime = AS_OF, stage: str = "judge"
) -> LlmRequest:
    return LlmRequest(stage, model, "v1", "hash", "system text", user, 300, as_of)


class Inner:
    def __init__(self, tokens_in: int = 1000, tokens_out: int = 100) -> None:
        self.calls = 0
        self._t = (tokens_in, tokens_out)

    async def complete(self, r: LlmRequest) -> LlmReply:
        self.calls += 1
        return LlmReply('{"ok":true}', self._t[0], self._t[1], r.model)


class TestRequest:
    def test_the_hash_covers_the_model_the_system_prompt_the_user_message_and_the_allowance(
        self,
    ) -> None:
        base = request().request_hash

        assert request(model=GATEWAY_MODEL).request_hash != base
        assert request(user='{"a":2}').request_hash != base
        assert (
            LlmRequest(
                "judge", OPENAI_MODEL, "v1", "hash", "other", '{"a":1}', 300, AS_OF
            ).request_hash
            != base
        )
        assert (
            LlmRequest(
                "judge", OPENAI_MODEL, "v1", "hash", "system text", '{"a":1}', 301, AS_OF
            ).request_hash
            != base
        )
        assert (
            request(as_of=AS_OF.replace(hour=9)).request_hash == base
        )  # the clock is not the question

    def test_a_request_needs_an_aware_time_and_room_to_answer(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            request(as_of=datetime(2024, 6, 3, 6, 0))
        with pytest.raises(ValueError, match="room"):
            LlmRequest("s", "m", "v", "h", "s", "u", 0, AS_OF)


class TestPricingAndBudget:
    def test_cost_is_per_million_tokens_and_converted_at_the_declared_rate(self) -> None:
        assert PRICES.cost_usd(OPENAI_MODEL, 1_000_000, 100_000) == Decimal("3.50")
        assert PRICES.cost_inr(OPENAI_MODEL, 1_000_000, 0) == Decimal("220.00")

    def test_a_model_with_no_declared_price_cannot_be_costed(self) -> None:
        with pytest.raises(ValueError, match="no declared price"):
            PRICES.cost_usd("gpt-5", 1, 1)

    def test_the_worst_case_counts_every_character_as_a_token_plus_the_full_output(self) -> None:
        worst = worst_case_usd(
            PRICES, OPENAI_MODEL, calls=150, prompt_chars=8000, max_output_tokens=250
        )

        assert worst == PRICES.cost_usd(OPENAI_MODEL, 8000, 250) * 150
        assert worst < Decimal("4")

    async def test_a_call_that_could_pass_the_ceiling_is_refused_before_it_is_made(self) -> None:
        inner, budget = Inner(), UsdBudget(Decimal("0.01"))
        client = BudgetedClient(inner, budget, PRICES)

        with pytest.raises(BudgetRefused, match="past the"):
            await client.complete(request(user="x" * 5000))

        assert inner.calls == 0 and budget.spent_usd == 0

    async def test_the_spend_is_the_real_token_cost_and_the_ceiling_holds_across_calls(
        self,
    ) -> None:
        inner, budget = Inner(1000, 100), UsdBudget(Decimal("0.0110"))
        client = BudgetedClient(inner, budget, PRICES)

        await client.complete(request())
        assert budget.spent_usd == Decimal("0.003500") and budget.calls == 1
        await client.complete(request())
        await client.complete(request())
        with pytest.raises(BudgetRefused):
            await client.complete(request())  # a fourth could take it past the ceiling

        assert inner.calls == 3 and budget.spent_usd <= budget.ceiling_usd

    async def test_calls_in_flight_count_against_the_ceiling_so_many_at_once_cannot_overshoot(
        self,
    ) -> None:
        import asyncio

        class Slow:
            async def complete(self, r: LlmRequest) -> LlmReply:
                await asyncio.sleep(0.01)
                return LlmReply("{}", 10, 10, r.model)

        one = PRICES.cost_usd(OPENAI_MODEL, len("system text") + len('{"a":1}'), 300)
        budget = UsdBudget(one * Decimal("2.5"))  # room for two in flight, not three
        client = BudgetedClient(Slow(), budget, PRICES)

        results = await asyncio.gather(*[client.complete(request()) for _ in range(3)],
                                       return_exceptions=True)  # fmt: skip

        assert sum(isinstance(r, BudgetRefused) for r in results) == 1
        assert budget.reserved_usd == 0 and budget.calls == 2

    async def test_a_call_that_failed_gives_its_reservation_back(self) -> None:
        class Broken:
            async def complete(self, r: LlmRequest) -> LlmReply:
                raise LlmTransportError("down")

        budget = UsdBudget(Decimal("1"))
        with pytest.raises(LlmTransportError):
            await BudgetedClient(Broken(), budget, PRICES).complete(request())

        assert budget.reserved_usd == 0 and budget.spent_usd == 0

    def test_the_spend_that_outlives_a_process_is_what_the_journal_holds(self) -> None:
        def rec(h: str) -> Recording:
            return Recording(
                h, "s", GATEWAY_MODEL, "v", "p", "t", "{}", 1_000_000, 0, GATEWAY_MODEL
            )

        journal = InMemoryJournal()
        journal.append(rec("a"))
        journal.append(rec("b"))

        assert journal_spend_usd(journal.recordings(), PRICES) == Decimal("0.30")

    def test_a_ceiling_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            UsdBudget(Decimal(0))


class TestJournal:
    async def test_a_new_question_reaches_the_model_and_a_repeat_is_free(self) -> None:
        inner, journal = Inner(), InMemoryJournal()
        client = JournaledClient(inner, journal, record=True)

        first, again = await client.complete(request()), await client.complete(request())

        assert inner.calls == 1 and len(journal) == 1
        assert not first.from_journal and again.from_journal and again.text == first.text

    async def test_replay_never_calls_the_model_and_a_new_question_is_refused(self) -> None:
        journal = InMemoryJournal()
        await JournaledClient(Inner(), journal, record=True).complete(request())

        replay = JournaledClient(None, journal, record=False)

        assert (await replay.complete(request())).from_journal
        with pytest.raises(NotRecorded):
            await replay.complete(request(user='{"other":1}'))

    async def test_a_changed_model_is_a_different_question(self) -> None:
        inner, journal = Inner(), InMemoryJournal()
        client = JournaledClient(inner, journal, record=True)

        await client.complete(request(model=OPENAI_MODEL))
        await client.complete(request(model=GATEWAY_MODEL))

        assert inner.calls == 2

    async def test_the_file_journal_survives_a_new_process(self, tmp_path: Path) -> None:
        path = tmp_path / "calls.jsonl"
        await JournaledClient(Inner(), JsonlJournal(path), record=True).complete(request())

        reopened = JsonlJournal(path)
        answered = await JournaledClient(None, reopened, record=False).complete(request())

        assert len(reopened) == 1 and answered.text == '{"ok":true}' and answered.tokens_in == 1000
        assert len(path.read_text().splitlines()) == 1

    def test_record_mode_needs_a_client(self) -> None:
        with pytest.raises(ValueError, match="needs a client"):
            JournaledClient(None, InMemoryJournal(), record=True)


class TestGuardAndTally:
    def configs(self) -> dict[str, JevConfig]:
        cutoff = JevConfig(model_knowledge_cutoff=date(2023, 10, 1))
        return {OPENAI_MODEL: cutoff, GATEWAY_MODEL: cutoff}

    async def test_nothing_before_the_cutoff_plus_ninety_days_reaches_the_model(self) -> None:
        inner = Inner()
        client = CutoffGuardedClient(inner, KnowledgeCutoffGuard(), self.configs())

        await client.complete(
            request(as_of=datetime(2024, 1, 1, 4, 0, tzinfo=UTC))
        )  # the first day
        with pytest.raises(JevLeakageError):
            await client.complete(request(as_of=datetime(2023, 12, 29, 4, 0, tzinfo=UTC)))

        assert inner.calls == 1

    async def test_a_recording_from_before_the_cutoff_is_refused_too_when_the_guard_is_outermost(
        self,
    ) -> None:
        journal = InMemoryJournal()
        early = request(as_of=datetime(2023, 6, 1, 4, 0, tzinfo=UTC))
        await JournaledClient(Inner(), journal, record=True).complete(early)
        guarded = CutoffGuardedClient(
            JournaledClient(None, journal, record=False), KnowledgeCutoffGuard(), self.configs()
        )

        with pytest.raises(JevLeakageError):
            await guarded.complete(early)

    async def test_a_model_without_a_declared_cutoff_is_refused(self) -> None:
        client = CutoffGuardedClient(Inner(), KnowledgeCutoffGuard(), {})

        with pytest.raises(ValueError, match="no declared knowledge cutoff"):
            await client.complete(request())

    async def test_the_tally_counts_replayed_answers_and_charges_them_at_the_declared_rate(
        self,
    ) -> None:
        tally = TokenTally()
        client = TallyingClient(
            JournaledClient(Inner(1000, 100), InMemoryJournal(), record=True), tally
        )

        await client.complete(request(stage="judge"))
        await client.complete(request(stage="judge"))  # from the journal, still costs what it cost
        await client.complete(request(stage="triage", model=GATEWAY_MODEL, user='{"b":1}'))

        assert tally.calls == {f"judge|{OPENAI_MODEL}": 2, f"triage|{GATEWAY_MODEL}": 1}
        by_stage = tally.cost_inr_by_stage(PRICES)
        assert by_stage["judge"] == PRICES.cost_inr(OPENAI_MODEL, 2000, 200)
        assert tally.cost_inr(PRICES) == by_stage["judge"] + by_stage["triage"]


def http_client(
    handler: object,
    attempts: int = 2,
    key: str = "sk-test",
    sleeper: AdvancingSleeper | None = None,
) -> OpenAiCompatibleClient:
    class Factory:
        def create(self) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                base_url="https://api.openai.com", transport=httpx.MockTransport(handler)
            )  # type: ignore[arg-type]

    clock = FixedClock(AS_OF)
    return OpenAiCompatibleClient(
        "https://api.openai.com",
        key,
        attempts=attempts,
        factory=Factory(),
        sleeper=sleeper or AdvancingSleeper(clock),
    )


def ok_body(
    text: str = '{"ok":true}', usage: dict[str, int] | None = None, model: str = OPENAI_MODEL
) -> dict[str, object]:
    return {
        "model": model, "choices": [{"message": {"content": text}}],
        "usage": usage if usage is not None else {"prompt_tokens": 120, "completion_tokens": 30},
    }  # fmt: skip


class TestHttpClient:
    async def test_the_call_is_deterministic_json_mode_with_the_pinned_model_and_the_key(
        self,
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(r: httpx.Request) -> httpx.Response:
            seen.append(r)
            return httpx.Response(200, json=ok_body())

        got = await http_client(handler).complete(request())

        body = json.loads(seen[0].content)
        assert body["model"] == OPENAI_MODEL and body["temperature"] == 0
        assert body["response_format"] == {"type": "json_object"} and body["max_tokens"] == 300
        assert seen[0].headers["authorization"] == "Bearer sk-test"
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        assert "as_of" not in json.dumps(body) and "2024" not in json.dumps(body["messages"])
        assert (got.text, got.tokens_in, got.tokens_out, got.model) == (
            '{"ok":true}',
            120,
            30,
            OPENAI_MODEL,
        )

    async def test_a_reply_without_token_usage_is_refused_because_it_could_not_be_charged(
        self,
    ) -> None:
        def handler(r: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=ok_body(usage={}))

        with pytest.raises(LlmTransportError):
            await http_client(handler).complete(request())

    async def test_a_server_error_is_retried_once_and_a_client_error_is_not(self) -> None:
        codes = iter([500, 200])
        hits = []

        def flaky(r: httpx.Request) -> httpx.Response:
            hits.append(1)
            code = next(codes)
            return httpx.Response(code, json=ok_body() if code == 200 else {})

        assert (await http_client(flaky).complete(request())).text == '{"ok":true}' and len(
            hits
        ) == 2

        denied: list[int] = []

        def unauthorised(r: httpx.Request) -> httpx.Response:
            denied.append(1)
            return httpx.Response(401, json={})

        with pytest.raises(LlmTransportError):
            await http_client(unauthorised, attempts=3).complete(request())
        assert len(denied) == 1

    async def test_a_rate_limit_is_retried(self) -> None:
        codes = iter([429, 200])

        def handler(r: httpx.Request) -> httpx.Response:
            code = next(codes)
            return httpx.Response(code, json=ok_body() if code == 200 else {})

        assert (await http_client(handler).complete(request())).tokens_out == 30

    async def test_a_rate_limit_waits_what_the_server_asks_and_backs_off_when_it_does_not(
        self,
    ) -> None:
        sleeper = AdvancingSleeper(FixedClock(AS_OF))
        replies = iter([(429, {"retry-after": "20"}), (429, {}), (429, {}), (200, {})])

        def handler(r: httpx.Request) -> httpx.Response:
            code, headers = next(replies)
            return httpx.Response(code, json=ok_body() if code == 200 else {}, headers=headers)

        await http_client(handler, attempts=6, sleeper=sleeper).complete(request())

        assert sleeper.sleeps == [20.0, 4.0, 8.0]  # the server's 20 s, then 2^1 x 2 and 2^2 x 2

    def test_the_key_comes_from_the_caller_and_is_required(self) -> None:
        with pytest.raises(ConfigurationError, match="API key"):
            OpenAiCompatibleClient("https://api.openai.com", "")
