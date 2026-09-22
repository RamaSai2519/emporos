from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.opportunity.models import OpportunityCandidate, RejectedCandidate
from emporos.strategies.regime import MarketRegime
from tests.support.strategies import T0, make_signal


def _candidate(
    side: OrderSide = OrderSide.BUY,
    entry: str = "100",
    stop: str = "98",
    target: str = "104",
    confidence: Decimal = Decimal(1),
) -> OpportunityCandidate:
    return OpportunityCandidate(
        strategy_name="momentum_v1",
        instrument_id="NSE:1001",
        signal=make_signal(side=side, price=entry),
        entry=Money.of(entry),
        stop=Money.of(stop),
        target=Money.of(target),
        regime=MarketRegime.TRENDING,
        generated_at=T0,
        confidence=confidence,
    )


def test_a_long_candidate_needs_stop_below_and_target_above_entry() -> None:
    with pytest.raises(ValueError, match="stop < entry < target"):
        _candidate(side=OrderSide.BUY, entry="100", stop="101", target="104")


def test_a_short_candidate_needs_target_below_and_stop_above_entry() -> None:
    with pytest.raises(ValueError, match="target < entry < stop"):
        _candidate(side=OrderSide.SELL, entry="100", stop="98", target="95")


def test_stop_must_be_a_positive_price() -> None:
    with pytest.raises(ValueError, match="positive prices"):
        _candidate(side=OrderSide.BUY, entry="100", stop="0", target="104")


def test_a_short_candidate_with_correctly_ordered_prices_is_valid() -> None:
    candidate = _candidate(side=OrderSide.SELL, entry="100", stop="102", target="96")
    assert candidate.risk_per_share == Money.of("2")
    assert candidate.reward_per_share == Money.of("4")


def test_confidence_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValueError, match="confidence"):
        _candidate(confidence=Decimal("1.5"))


def test_generated_at_must_be_timezone_aware() -> None:
    naive = T0.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        OpportunityCandidate(
            strategy_name="momentum_v1",
            instrument_id="NSE:1001",
            signal=make_signal(),
            entry=Money.of("100"),
            stop=Money.of("98"),
            target=Money.of("104"),
            regime=None,
            generated_at=naive,
        )


def test_expected_edge_is_the_reward_to_risk_ratio() -> None:
    candidate = _candidate(entry="100", stop="98", target="104")
    assert candidate.expected_edge == Decimal(2)


def test_score_weights_edge_by_confidence() -> None:
    candidate = _candidate(entry="100", stop="98", target="104", confidence=Decimal("0.5"))
    assert candidate.score == Decimal(1)


def test_direction_mirrors_the_underlying_signal_side() -> None:
    candidate = _candidate(side=OrderSide.SELL, entry="100", stop="102", target="96")
    assert candidate.direction is OrderSide.SELL


def test_rejected_candidate_requires_a_reason() -> None:
    with pytest.raises(ValueError, match="why"):
        RejectedCandidate(strategy_name="momentum_v1", instrument_id="NSE:1001", reason="  ", ts=T0)
