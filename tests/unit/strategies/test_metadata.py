from __future__ import annotations

import pytest

from emporos.domain.candles import Timeframe
from emporos.strategies.metadata import (
    DeploymentStatus,
    StrategyMetadata,
    ValidationStatus,
)
from emporos.strategies.regime import MarketRegime
from emporos.strategies.registry import StrategyRegistry, UnknownStrategyError
from tests.support.strategies import ThresholdStrategy


def _metadata(**overrides: object) -> StrategyMetadata:
    defaults: dict[object, object] = {
        "version": "v1",
        "supported_timeframes": frozenset({Timeframe.M5}),
        "supported_regimes": frozenset({MarketRegime.TRENDING}),
    }
    defaults.update(overrides)
    return StrategyMetadata(**defaults)  # type: ignore[arg-type]


def test_a_strategy_needs_a_version() -> None:
    with pytest.raises(ValueError, match="version"):
        _metadata(version="")


def test_empty_timeframes_means_unconstrained() -> None:
    metadata = _metadata(supported_timeframes=frozenset())

    assert metadata.supports(
        timeframe=Timeframe.M15, regime=MarketRegime.TRENDING, instrument_id="NSE:1"
    )


def test_empty_regimes_means_unconstrained() -> None:
    metadata = _metadata(supported_regimes=frozenset())

    assert metadata.supports(
        timeframe=Timeframe.M5, regime=MarketRegime.RANGING, instrument_id="NSE:1"
    )


def test_supports_checks_timeframe_regime_and_instrument() -> None:
    metadata = _metadata(supported_instrument_ids=frozenset({"NSE:1"}))

    assert metadata.supports(
        timeframe=Timeframe.M5, regime=MarketRegime.TRENDING, instrument_id="NSE:1"
    )
    assert not metadata.supports(
        timeframe=Timeframe.M15, regime=MarketRegime.TRENDING, instrument_id="NSE:1"
    )
    assert not metadata.supports(
        timeframe=Timeframe.M5, regime=MarketRegime.RANGING, instrument_id="NSE:1"
    )
    assert not metadata.supports(
        timeframe=Timeframe.M5, regime=MarketRegime.TRENDING, instrument_id="NSE:2"
    )


def test_no_fixed_universe_means_any_instrument_is_eligible() -> None:
    metadata = _metadata()

    assert metadata.supports(
        timeframe=Timeframe.M5, regime=MarketRegime.TRENDING, instrument_id="NSE:99"
    )


def test_default_status_is_research_and_candidate() -> None:
    metadata = _metadata()

    assert metadata.validation_status is ValidationStatus.RESEARCH
    assert metadata.deployment_status is DeploymentStatus.CANDIDATE


def test_registry_stores_and_returns_metadata() -> None:
    registry = StrategyRegistry()
    metadata = _metadata(version="v2", validation_status=ValidationStatus.BACKTESTED)

    registry.register(ThresholdStrategy, metadata)

    assert registry.metadata("threshold") is metadata


def test_registry_gives_unknown_metadata_when_none_declared() -> None:
    registry = StrategyRegistry()
    registry.register(ThresholdStrategy)

    metadata = registry.metadata("threshold")

    assert metadata.version == "unknown"
    assert metadata.validation_status is ValidationStatus.RESEARCH


def test_registry_metadata_for_unknown_name_raises() -> None:
    registry = StrategyRegistry()
    with pytest.raises(UnknownStrategyError):
        registry.metadata("nonesuch")


def test_register_all_accepts_strategy_metadata_pairs() -> None:
    registry = StrategyRegistry()
    metadata = _metadata()

    registry.register_all([(ThresholdStrategy, metadata)])

    assert registry.metadata("threshold") is metadata
