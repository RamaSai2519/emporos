from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.opportunity.allocator import Allocation
from emporos.opportunity.deployment_gate import DeploymentGate
from emporos.opportunity.models import OpportunityCandidate
from emporos.strategies.metadata import DeploymentStatus, StrategyMetadata
from emporos.strategies.regime import MarketRegime
from emporos.strategies.registry import StrategyRegistry
from tests.support.strategies import INSTRUMENT, T0, ThresholdStrategy, make_signal


def _registry(status: DeploymentStatus) -> StrategyRegistry:
    registry = StrategyRegistry()
    metadata = StrategyMetadata(
        version="v1",
        supported_timeframes=frozenset(),
        supported_regimes=frozenset(),
        deployment_status=status,
    )
    registry.register(ThresholdStrategy, metadata)
    return registry


def _allocation(quantity: int = 100) -> Allocation:
    candidate = OpportunityCandidate(
        strategy_name="threshold",
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.M5,
        signal=make_signal(instrument_id=INSTRUMENT, price="100", quantity=quantity),
        entry=Money.of("100"),
        stop=Money.of("98"),
        target=Money.of("104"),
        regime=MarketRegime.TRENDING,
        generated_at=T0,
    )
    return Allocation(candidate, quantity)


def test_conservative_fraction_must_be_in_zero_to_one() -> None:
    with pytest.raises(ValueError, match="conservative_fraction"):
        DeploymentGate(_registry(DeploymentStatus.PRODUCTION), conservative_fraction=Decimal(0))


def test_production_status_passes_through_unscaled() -> None:
    gate = DeploymentGate(_registry(DeploymentStatus.PRODUCTION))

    result = gate.apply((_allocation(100),))

    assert len(result) == 1
    assert result[0].quantity == 100


def test_live_conservative_scales_down_by_the_default_fraction() -> None:
    gate = DeploymentGate(_registry(DeploymentStatus.LIVE_CONSERVATIVE))

    result = gate.apply((_allocation(100),))

    assert len(result) == 1
    assert result[0].quantity == 20  # 20% of 100


@pytest.mark.parametrize(
    "status", [DeploymentStatus.CANDIDATE, DeploymentStatus.PAPER, DeploymentStatus.RETIRED]
)
def test_ineligible_statuses_are_dropped_entirely(status: DeploymentStatus) -> None:
    gate = DeploymentGate(_registry(status))

    result = gate.apply((_allocation(100),))

    assert result == ()


def test_a_live_conservative_allocation_that_rounds_to_zero_is_dropped() -> None:
    gate = DeploymentGate(
        _registry(DeploymentStatus.LIVE_CONSERVATIVE), conservative_fraction=Decimal("0.01")
    )

    result = gate.apply((_allocation(5),))  # 5 * 0.01 = 0.05 -> floors to 0

    assert result == ()


def test_a_custom_conservative_fraction_is_respected() -> None:
    gate = DeploymentGate(
        _registry(DeploymentStatus.LIVE_CONSERVATIVE), conservative_fraction=Decimal("0.5")
    )

    result = gate.apply((_allocation(100),))

    assert result[0].quantity == 50


def test_an_empty_allocation_tuple_stays_empty() -> None:
    gate = DeploymentGate(_registry(DeploymentStatus.PRODUCTION))

    assert gate.apply(()) == ()
