"""The Dev run's wiring: prices from the declaration, the client stack, the variant table."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.cli.llm_commands import prompts_hash
from emporos.cli.llm_composition import LlmStack, declared_prices
from emporos.core.config import Settings
from emporos.eventtrader.llm.client import LlmRequest
from emporos.eventtrader.llm.http_clients import MINI_MODEL, OPENAI_MODEL
from emporos.eventtrader.llm.journal import JsonlJournal, NotRecorded, Recording
from emporos.eventtrader.variants import VARIANTS, variant
from emporos.jev.leakage import JevLeakageError

D = Decimal


def request(day: int = 2, year: int = 2024) -> LlmRequest:
    return LlmRequest(
        "triage", MINI_MODEL, "triage-v1", "h", "sys", "{}", 50,
        datetime(year, 3, day, 6, tzinfo=UTC),
    )  # fmt: skip


def stack(tmp_path: Path, *, record: bool = False) -> LlmStack:
    return LlmStack(
        Settings(_env_file=None),  # type: ignore[call-arg]
        tmp_path / "j.jsonl", declared_prices(), record=record, mini_ceiling_usd=D(1),
    )  # fmt: skip


def test_the_prices_are_the_declarations() -> None:
    prices = declared_prices()

    assert prices.usd_inr == D("88.00")
    assert prices.cost_usd(MINI_MODEL, 1_000_000, 1_000_000) == D("0.75")
    assert MINI_MODEL == "gpt-4o-mini-2024-07-18"
    assert prices.cost_usd(OPENAI_MODEL, 1_000_000, 1_000_000) == D("12.50")


async def test_replay_answers_from_the_journal_counts_the_tokens_and_never_calls(
    tmp_path: Path,
) -> None:
    JsonlJournal(tmp_path / "j.jsonl").append(
        Recording(
            request().request_hash,
            "triage",
            MINI_MODEL,
            "triage-v1",
            "h",
            "t",
            "{}",
            100,
            10,
            MINI_MODEL,
        )
    )
    s = stack(tmp_path)

    reply = await s.mini().complete(request())

    assert reply.text == "{}" and s.tally.cost_inr(declared_prices()) > 0
    assert (s.hits, s.fresh) == (1, 0)


async def test_replay_of_a_question_never_asked_is_refused_not_papered_over(tmp_path: Path) -> None:
    with pytest.raises(NotRecorded):
        await stack(tmp_path).mini().complete(request(day=3))


async def test_the_date_guard_stands_in_front_of_the_journal(tmp_path: Path) -> None:
    with pytest.raises(JevLeakageError):
        await stack(tmp_path).mini().complete(request(year=2023))


def test_recording_needs_the_openai_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from emporos.core.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        LlmStack(
            Settings(_env_file=None), tmp_path / "j", declared_prices(), record=True,  # type: ignore[call-arg]
            mini_ceiling_usd=D(1),
        ).mini()  # fmt: skip


def test_the_variants_are_the_declared_five_and_an_unknown_one_is_refused() -> None:
    assert list(VARIANTS) == [
        "v1_t60",
        "v2_t75",
        "v3_nopanel_t60",
        "v4_posture_t60",
        "v5_posture_t75",
    ]
    assert variant("v3_nopanel_t60").panel is False and variant("v5_posture_t75").posture is True
    with pytest.raises(ValueError, match="unknown variant"):
        variant("v9")


def test_the_prompt_hash_is_stable_and_short() -> None:
    assert prompts_hash() == prompts_hash() and len(prompts_hash()) == 16
