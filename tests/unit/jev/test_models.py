from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from emporos.jev.models import ABSTAIN, CONFIRM, JevDecision, JevRequest, failed_decision

T0 = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)


def _request(**overrides: object) -> JevRequest:
    defaults: dict[str, object] = {
        "symbol": "NSE:RELIANCE-EQ",
        "timeframe": "5m",
        "regime": "trending",
        "strategy_name": "momentum_v1",
        "direction": "BUY",
        "entry": Decimal(100),
        "stop": Decimal(98),
        "target": Decimal(104),
        "expected_edge": Decimal(2),
        "confidence": Decimal(1),
        "as_of": T0,
    }
    defaults.update(overrides)
    return JevRequest(**defaults)  # type: ignore[arg-type]


def test_a_request_needs_a_symbol() -> None:
    with pytest.raises(ValueError, match="symbol"):
        _request(symbol="")


def test_a_request_needs_a_strategy_name() -> None:
    with pytest.raises(ValueError, match="strategy name"):
        _request(strategy_name="")


def test_feature_maps_are_immutable() -> None:
    request = _request(features={"rsi": Decimal(70)})
    with pytest.raises(TypeError):
        request.features["rsi"] = Decimal(0)  # type: ignore[index]


def _decision(**overrides: object) -> JevDecision:
    defaults: dict[str, object] = {
        "decision": CONFIRM,
        "confidence": Decimal("0.8"),
        "provider": "test",
        "model": "test-model",
        "config_version": "v1",
        "requested_at": T0,
        "latency_ms": 10,
    }
    defaults.update(overrides)
    return JevDecision(**defaults)  # type: ignore[arg-type]


def test_requested_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _decision(requested_at=T0.replace(tzinfo=None))


def test_confidence_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValueError, match="confidence"):
        _decision(confidence=Decimal("1.5"))


def test_latency_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="latency_ms"):
        _decision(latency_ms=-1)


def test_tokens_used_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="tokens_used"):
        _decision(tokens_used=-1)


def test_tokens_used_defaults_to_none() -> None:
    assert _decision().tokens_used is None


def test_ok_is_true_without_an_error() -> None:
    assert _decision().ok is True


def test_ok_is_false_with_an_error() -> None:
    assert _decision(error="boom").ok is False


def test_is_confirm_requires_ok_and_confirm_decision() -> None:
    assert _decision(decision=CONFIRM).is_confirm is True
    assert _decision(decision=CONFIRM, error="boom").is_confirm is False
    assert _decision(decision=ABSTAIN).is_confirm is False


def test_failed_decision_is_always_abstain_with_the_error_recorded() -> None:
    decision = failed_decision("vercel_gateway", error="timeout", requested_at=T0, latency_ms=5)

    assert decision.decision == ABSTAIN
    assert decision.confidence is None
    assert decision.error == "timeout"
    assert decision.ok is False


def test_as_of_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="as_of"):
        _request(as_of=T0.replace(tzinfo=None))


def test_the_payload_never_carries_as_of() -> None:
    assert "as_of" not in _request().payload()


def test_the_request_hash_is_stable_and_ignores_as_of() -> None:
    first = _request()
    later = _request(as_of=T0.replace(hour=5))

    assert first.request_hash() == later.request_hash()
    assert len(first.request_hash()) == 64


def test_the_request_hash_changes_with_any_payload_field() -> None:
    assert _request().request_hash() != _request(entry=Decimal(101)).request_hash()
    assert _request().request_hash() != _request(features={"rsi": Decimal(70)}).request_hash()


def test_a_failed_decision_carries_provenance() -> None:
    decision = failed_decision(
        "p", error="x", requested_at=T0, prompt_version="v1", prompt_hash="h", request_hash="r"
    )

    assert (decision.prompt_version, decision.prompt_hash, decision.request_hash) == (
        "v1",
        "h",
        "r",
    )
