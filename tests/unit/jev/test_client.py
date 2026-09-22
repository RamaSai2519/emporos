from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from emporos.broker.backoff import BackoffPolicy
from emporos.core.clock import FixedClock
from emporos.jev.client import VercelGatewayJevClient
from emporos.jev.config import JevConfig
from emporos.jev.models import ABSTAIN, CONFIRM, JevRequest
from tests.support.fakes import AdvancingSleeper, FixedJitter, ScriptedHttpServer

T0 = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)
API_KEY = "vck_test_key"


class _FixedClientFactory:
    def __init__(self, server: ScriptedHttpServer) -> None:
        self._server = server

    def create(self) -> httpx.AsyncClient:
        return self._server.client()


def _chat_reply(
    decision: str = CONFIRM, confidence: str = "0.8", usage: dict[str, int] | None = None
) -> httpx.Response:
    body: dict[str, object] = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"decision": decision, "confidence": confidence, "reason": "ok"}
                    )
                }
            }
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, content=json.dumps(body))


def _request() -> JevRequest:
    return JevRequest(
        symbol="NSE:RELIANCE-EQ",
        timeframe="5m",
        regime="trending",
        strategy_name="momentum_v1",
        direction="BUY",
        entry=Decimal(100),
        stop=Decimal(98),
        target=Decimal(104),
        expected_edge=Decimal(2),
        confidence=Decimal(1),
    )


def _client(
    server: ScriptedHttpServer,
    *,
    clock: FixedClock | None = None,
    sleeper: AdvancingSleeper | None = None,
    max_retries: int = 0,
) -> VercelGatewayJevClient:
    clock = clock or FixedClock(T0)
    return VercelGatewayJevClient(
        JevConfig(enabled=True, max_retries=max_retries),
        API_KEY,
        clock=clock,
        client_factory=_FixedClientFactory(server),
        backoff=BackoffPolicy(base_delay=0.1, max_delay=1.0, max_attempts=5),
        jitter=FixedJitter(0.0),
        sleeper=sleeper or AdvancingSleeper(clock),
    )


async def test_a_confirm_reply_is_parsed_into_a_confirm_decision() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(_chat_reply(CONFIRM))

    decision = await _client(server).decide(_request())

    assert decision.decision == CONFIRM
    assert decision.confidence == Decimal("0.8")
    assert decision.ok is True
    assert decision.provider == "vercel_gateway"


async def test_sends_the_bearer_token_and_json_payload() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(_chat_reply())

    await _client(server).decide(_request())

    request = server.requests[0]
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.method == "POST"
    assert request.url.path == "/v1/chat/completions"
    body = json.loads(request.content)
    assert body["messages"][1]["content"]
    context = json.loads(body["messages"][1]["content"])
    assert context["symbol"] == "NSE:RELIANCE-EQ"
    assert context["entry"] == "100"


async def test_token_usage_is_captured_as_a_cost_proxy() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(
        _chat_reply(usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
    )

    decision = await _client(server).decide(_request())

    assert decision.tokens_used == 120


async def test_missing_usage_leaves_tokens_used_as_none() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(_chat_reply())

    decision = await _client(server).decide(_request())

    assert decision.tokens_used is None


async def test_a_non_numeric_confidence_is_treated_as_no_confidence() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(
        _chat_reply(CONFIRM, confidence="very sure")
    )

    decision = await _client(server).decide(_request())

    assert decision.decision == CONFIRM
    assert decision.confidence is None


async def test_an_unrecognized_decision_value_fails_safe_to_abstain() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(
        _chat_reply(decision="maybe")
    )

    decision = await _client(server).decide(_request())

    assert decision.decision == ABSTAIN
    assert decision.ok is True  # a weird-but-parseable reply is not a failure


async def test_malformed_json_content_is_retried_then_reported_as_a_failure() -> None:
    bad = httpx.Response(
        200, content=json.dumps({"choices": [{"message": {"content": "not json"}}]})
    )
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(bad, bad)

    decision = await _client(server, max_retries=1).decide(_request())

    assert decision.ok is False
    assert decision.decision == ABSTAIN
    assert "could not parse" in (decision.error or "")
    assert len(server.requests) == 2


async def test_a_timeout_is_retried_and_then_reported_as_a_failure_never_raised() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(
        httpx.TimeoutException("timed out"), httpx.TimeoutException("timed out")
    )

    decision = await _client(server, max_retries=1).decide(_request())

    assert decision.ok is False
    assert decision.decision == ABSTAIN
    assert "Timeout" in (decision.error or "")
    assert len(server.requests) == 2


async def test_a_retry_waits_using_the_injected_backoff_and_sleeper() -> None:
    clock = FixedClock(T0)
    sleeper = AdvancingSleeper(clock)
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(
        httpx.TimeoutException("timed out"), _chat_reply(CONFIRM)
    )

    decision = await _client(server, clock=clock, sleeper=sleeper, max_retries=1).decide(_request())

    assert decision.ok is True
    assert len(sleeper.sleeps) == 1


async def test_a_5xx_response_is_treated_as_a_failure_not_raised() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(
        httpx.Response(500, content="server error")
    )

    decision = await _client(server).decide(_request())

    assert decision.ok is False
    assert decision.decision == ABSTAIN


async def test_concurrency_is_bounded_by_max_concurrency() -> None:
    server = ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh").queue(
        _chat_reply(), _chat_reply()
    )
    client = VercelGatewayJevClient(
        JevConfig(enabled=True, max_concurrency=1),
        API_KEY,
        clock=FixedClock(T0),
        client_factory=_FixedClientFactory(server),
    )

    results = await asyncio.gather(client.decide(_request()), client.decide(_request()))

    assert all(decision.ok for decision in results)
