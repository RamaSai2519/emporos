from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from emporos.core.clock import FixedClock
from emporos.jev.models import ABSTAIN, JevRequest
from emporos.jev.null_provider import NullJevProvider

T0 = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)


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


async def test_null_provider_always_abstains() -> None:
    provider = NullJevProvider(FixedClock(T0))

    decision = await provider.decide(_request())

    assert decision.decision == ABSTAIN
    assert decision.confidence is None
    assert decision.provider == "null"
    assert decision.latency_ms == 0
    assert decision.ok is True


async def test_null_provider_uses_the_injected_clock_deterministically() -> None:
    provider = NullJevProvider(FixedClock(T0))

    decision = await provider.decide(_request())

    assert decision.requested_at == T0
