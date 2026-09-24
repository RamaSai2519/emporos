"""Shared Jev doubles and builders for tests: a request factory and a provider that records what
it was asked and never touches a network."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from emporos.jev.models import CONFIRM, JevDecision, JevRequest

JEV_T0 = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)


def make_jev_request(**overrides: object) -> JevRequest:
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
        "as_of": JEV_T0,
    }
    defaults.update(overrides)
    return JevRequest(**defaults)  # type: ignore[arg-type]


def make_jev_decision(
    request: JevRequest | None = None,
    decision: str = CONFIRM,
    confidence: str | None = "0.8",
    **overrides: object,
) -> JevDecision:
    defaults: dict[str, object] = {
        "decision": decision,
        "confidence": Decimal(confidence) if confidence is not None else None,
        "provider": "test",
        "model": "test-model",
        "config_version": None,
        "requested_at": JEV_T0,
        "latency_ms": 5,
        "tokens_used": 100,
        "prompt_version": "v1",
        "prompt_hash": "p" * 64,
        "request_hash": request.request_hash() if request is not None else None,
    }
    defaults.update(overrides)
    return JevDecision(**defaults)  # type: ignore[arg-type]


class RecordingProvider:
    """A `JevProvider` that answers a fixed decision and remembers every request it saw."""

    def __init__(self, decision: JevDecision | None = None) -> None:
        self._decision = decision
        self.requests: list[JevRequest] = []

    async def decide(self, request: JevRequest) -> JevDecision:
        self.requests.append(request)
        return self._decision or make_jev_decision(request)
