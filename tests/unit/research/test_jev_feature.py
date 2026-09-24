"""EM-187: Jev's recorded confidence as a causal research feature that can only read the journal."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from tests.support.jev import RecordingProvider
from tests.unit.research.conftest import START, bars

from emporos.domain.jev_records import JevDecisionRecord
from emporos.jev.leakage import SymbolAnonymiser
from emporos.jev.replay import InMemoryJevDecisionJournal
from emporos.research.features import FeatureRegistry, FeatureSeries
from emporos.research.jev_feature import (
    JEV_CONFIDENCE,
    JevConfidenceFeature,
    JevConfidenceIndex,
    jev_confidence_definition,
    register_jev_confidence,
)

D = Decimal
MODEL = "vendor/model-a"
PROMPT = "p" * 64
PSEUDONYM = SymbolAnonymiser(RecordingProvider(), salt="run").pseudonym
BAR = timedelta(minutes=5)


def _record(
    minutes: float,
    decision: str = "confirm",
    confidence: str | None = "0.8",
    symbol: str = "NSE:1",
    model: str = MODEL,
    prompt_hash: str = PROMPT,
) -> JevDecisionRecord:
    return JevDecisionRecord(
        request_hash=f"r{minutes}{symbol}{decision}",
        as_of=START + timedelta(minutes=minutes),
        symbol=PSEUDONYM(symbol),
        strategy="momentum_v1",
        mode="confirmation",
        decision=decision,
        confidence=None if confidence is None else D(confidence),
        provider="p",
        model=model,
        prompt_version="v1",
        prompt_hash=prompt_hash,
        tokens_used=1,
        latency_ms=1,
        recorded_at=START,
    )


def _values(*records: JevDecisionRecord, n: int = 4) -> list[Decimal | None]:
    index = JevConfidenceIndex.from_records(records, MODEL, PROMPT)
    return FeatureSeries(JevConfidenceFeature(index, PSEUDONYM)).compute(bars([100] * n))


def test_a_decision_inside_a_bar_is_that_bars_value() -> None:
    # bar 1 opens at +5m and closes at +10m; a decision at +7m is known by its close
    assert _values(_record(7)) == [None, D("0.8"), None, None]


def test_a_decision_at_a_bars_close_is_known_then_and_one_at_its_open_is_the_last_bars() -> None:
    assert _values(_record(10)) == [None, D("0.8"), None, None]  # closes bar 1
    assert _values(_record(5)) == [D("0.8"), None, None, None]  # at bar 1's open: bar 0's


def test_a_later_decision_never_leaks_into_an_earlier_bar() -> None:
    values = _values(_record(17))  # inside bar 3

    assert values[:3] == [None, None, None]
    assert values[3] == D("0.8")


def test_a_bar_without_a_decision_is_undefined_not_filled_from_an_older_one() -> None:
    values = _values(_record(2), n=4)

    assert values == [D("0.8"), None, None, None]


def test_a_reject_is_negative_and_an_abstain_has_no_opinion() -> None:
    assert _values(_record(2, "reject", "0.9"))[0] == D("-0.9")
    assert _values(_record(2, "abstain", None))[0] is None
    assert _values(_record(2, "confirm", None))[0] is None


def test_the_latest_decision_in_a_bar_wins() -> None:
    assert _values(_record(1, confidence="0.3"), _record(3, confidence="0.6"))[0] == D("0.6")


def test_only_the_named_model_and_prompt_are_read() -> None:
    other_model = _record(2, model="vendor/other")
    other_prompt = _record(2, prompt_hash="q" * 64)

    assert _values(other_model, other_prompt) == [None, None, None, None]


def test_instruments_are_kept_apart() -> None:
    values = _values(_record(2, symbol="NSE:2"))

    assert values == [None, None, None, None]  # the bars are for NSE:1


def test_it_reads_only_recorded_decisions_from_a_journal() -> None:
    journal = InMemoryJevDecisionJournal()

    async def fill() -> None:
        await journal.append(_record(2))
        await journal.append(replace(_record(7), request_hash="other"))

    asyncio.run(fill())

    index = JevConfidenceIndex.from_records(journal.records(), MODEL, PROMPT)
    values = FeatureSeries(JevConfidenceFeature(index, PSEUDONYM)).compute(bars([100] * 3))

    assert values == [D("0.8"), D("0.8"), None]


def test_it_registers_as_a_versioned_feature_bound_to_a_model_and_prompt() -> None:
    registry = FeatureRegistry()
    index = JevConfidenceIndex.from_records([], MODEL, PROMPT)

    register_jev_confidence(registry, index, PSEUDONYM, MODEL, PROMPT)

    definition, _ = registry.get(JEV_CONFIDENCE, "1")
    assert definition == jev_confidence_definition(MODEL, PROMPT)
    assert definition.content_hash != jev_confidence_definition(MODEL, "z" * 64).content_hash
