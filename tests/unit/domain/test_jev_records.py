from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emporos.domain.jev_records import JevDecisionRecord

T0 = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)


def _record(**overrides: object) -> JevDecisionRecord:
    base = JevDecisionRecord(
        request_hash="r" * 64,
        as_of=T0,
        symbol="INSTR_ab12cd34",
        strategy="momentum_v1",
        mode="confirmation",
        decision="confirm",
        confidence=Decimal("0.8"),
        provider="vercel_gateway",
        model="vendor/model-a",
        prompt_version="v1",
        prompt_hash="p" * 64,
        tokens_used=120,
        latency_ms=300,
        recorded_at=T0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_the_key_is_the_questions_identity() -> None:
    record = _record()

    assert record.key == ("r" * 64, "p" * 64, "vendor/model-a")


@pytest.mark.parametrize("field", ["request_hash", "symbol", "strategy", "decision", "model"])
def test_required_text_fields_cannot_be_empty(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _record(**{field: ""})


def test_the_prompt_provenance_is_required() -> None:
    with pytest.raises(ValueError, match="prompt"):
        _record(prompt_hash="")


def test_times_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _record(as_of=T0.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timezone-aware"):
        _record(recorded_at=T0.replace(tzinfo=None))


def test_confidence_must_be_a_probability() -> None:
    with pytest.raises(ValueError, match="confidence"):
        _record(confidence=Decimal("1.5"))
    assert _record(confidence=None).confidence is None
