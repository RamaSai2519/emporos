from __future__ import annotations

import json

import httpx
import pytest

from emporos.core.clock import FixedClock
from emporos.jev.client import VercelGatewayJevClient
from emporos.jev.config import JevConfig
from emporos.jev.models import ABSTAIN, CONFIRM
from emporos.jev.prompts import DEFAULT_PROMPT, JevPrompt
from emporos.jev.replay import (
    NOT_RECORDED,
    InMemoryJevDecisionJournal,
    RecordingJevProvider,
    RecordMissesJevProvider,
    ReplayJevProvider,
)
from tests.support.fakes import AdvancingSleeper, FixedJitter, ScriptedHttpServer
from tests.support.jev import JEV_T0, RecordingProvider, make_jev_decision, make_jev_request

MODEL = JevConfig().model


class _Factory:
    def __init__(self, server: ScriptedHttpServer) -> None:
        self._server = server

    def create(self) -> httpx.AsyncClient:
        return self._server.client()


def _reply(decision: str = CONFIRM, confidence: str = "0.8") -> httpx.Response:
    content = json.dumps({"decision": decision, "confidence": confidence, "reason": "ok"})
    body = {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 90}}
    return httpx.Response(200, content=json.dumps(body))


def _client(
    server: ScriptedHttpServer, prompt: JevPrompt = DEFAULT_PROMPT
) -> VercelGatewayJevClient:
    clock = FixedClock(JEV_T0)
    return VercelGatewayJevClient(
        JevConfig(enabled=True, max_retries=0),
        "key",
        clock=clock,
        client_factory=_Factory(server),
        prompt=prompt,
        jitter=FixedJitter(0.0),
        sleeper=AdvancingSleeper(clock),
    )


def _server() -> ScriptedHttpServer:
    return ScriptedHttpServer(base_url="https://gateway.ai.vercel.sh")


async def test_record_then_replay_gives_the_identical_decision_with_no_network() -> None:
    journal = InMemoryJevDecisionJournal()
    live = _server().queue(_reply())
    request = make_jev_request()
    recorded = await RecordingJevProvider(_client(live), journal, "confirmation").decide(request)

    silent = _server()  # nothing queued: any call would fail loudly
    replayed = await ReplayJevProvider(journal, DEFAULT_PROMPT, MODEL).decide(request)

    assert replayed == recorded
    assert len(live.requests) == 1
    assert silent.requests == []
    assert len(journal) == 1


async def test_one_recording_serves_every_mode() -> None:
    journal = InMemoryJevDecisionJournal()
    request = make_jev_request()
    await RecordingJevProvider(_client(_server().queue(_reply())), journal, "ranking").decide(
        request
    )

    replay = ReplayJevProvider(journal, DEFAULT_PROMPT, MODEL)

    assert (await replay.decide(request)).decision == CONFIRM  # what a confirmation run reads too


async def test_a_question_never_recorded_is_a_failed_decision() -> None:
    replay = ReplayJevProvider(InMemoryJevDecisionJournal(), DEFAULT_PROMPT, MODEL)
    request = make_jev_request()

    decision = await replay.decide(request)

    assert decision.ok is False
    assert decision.error == NOT_RECORDED
    assert decision.decision == ABSTAIN
    assert decision.requested_at == request.as_of
    assert decision.request_hash == request.request_hash()


async def test_a_changed_prompt_is_a_different_question() -> None:
    journal = InMemoryJevDecisionJournal()
    request = make_jev_request()
    await RecordingJevProvider(_client(_server().queue(_reply())), journal, "confirmation").decide(
        request
    )
    other = JevPrompt(version="v2", system_text="A different instruction.")

    decision = await ReplayJevProvider(journal, other, MODEL).decide(request)

    assert decision.error == NOT_RECORDED


async def test_a_changed_model_is_a_different_question() -> None:
    journal = InMemoryJevDecisionJournal()
    request = make_jev_request()
    await RecordingJevProvider(_client(_server().queue(_reply())), journal, "confirmation").decide(
        request
    )

    decision = await ReplayJevProvider(journal, DEFAULT_PROMPT, "vendor/other").decide(request)

    assert decision.error == NOT_RECORDED


async def test_a_failed_decision_is_returned_but_not_journalled() -> None:
    journal = InMemoryJevDecisionJournal()
    failing = _server().queue(httpx.Response(500))

    decision = await RecordingJevProvider(_client(failing), journal, "confirmation").decide(
        make_jev_request()
    )

    assert decision.ok is False
    assert len(journal) == 0


async def test_the_first_recorded_answer_stands() -> None:
    journal = InMemoryJevDecisionJournal()
    request = make_jev_request()
    stamp = {"prompt_hash": DEFAULT_PROMPT.content_hash, "model": MODEL}
    first = RecordingJevProvider(
        RecordingProvider(make_jev_decision(request, **stamp)), journal, "m"
    )
    second = RecordingJevProvider(
        RecordingProvider(make_jev_decision(request, decision="reject", **stamp)), journal, "m"
    )
    await first.decide(request)
    await second.decide(request)

    replayed = await ReplayJevProvider(journal, DEFAULT_PROMPT, MODEL).decide(request)

    assert replayed.decision == CONFIRM
    assert len(journal) == 1


async def test_a_replayed_decision_carries_the_recorded_cost() -> None:
    journal = InMemoryJevDecisionJournal()
    request = make_jev_request()
    await RecordingJevProvider(_client(_server().queue(_reply())), journal, "confirmation").decide(
        request
    )

    decision = await ReplayJevProvider(journal, DEFAULT_PROMPT, MODEL).decide(request)

    assert decision.tokens_used == 90
    assert decision.prompt_hash == DEFAULT_PROMPT.content_hash


async def test_recording_a_decision_without_provenance_is_refused() -> None:
    request = make_jev_request()
    unprovenanced = make_jev_decision(request, prompt_hash="", prompt_version="")

    with pytest.raises(ValueError, match="prompt"):
        await RecordingJevProvider(
            RecordingProvider(unprovenanced), InMemoryJevDecisionJournal(), "m"
        ).decide(request)


async def test_record_mode_asks_the_model_once_per_distinct_question() -> None:
    journal = InMemoryJevDecisionJournal()
    live = _server().queue(_reply())  # one reply only: a second live call would fail loudly
    provider = RecordMissesJevProvider(
        journal, _client(live), DEFAULT_PROMPT, MODEL, "confirmation"
    )
    request = make_jev_request()

    first = await provider.decide(request)
    second = await provider.decide(request)

    assert first == second
    assert len(live.requests) == 1
    assert len(journal) == 1


async def test_record_mode_serves_an_already_recorded_question_without_the_model() -> None:
    journal = InMemoryJevDecisionJournal()
    request = make_jev_request()
    await RecordingJevProvider(_client(_server().queue(_reply())), journal, "m").decide(request)
    silent = _server()

    decision = await RecordMissesJevProvider(
        journal, _client(silent), DEFAULT_PROMPT, MODEL, "ranking"
    ).decide(request)

    assert decision.ok
    assert silent.requests == []
