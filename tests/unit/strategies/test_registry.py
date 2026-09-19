from __future__ import annotations

import pytest

from emporos.strategies.base import Strategy
from emporos.strategies.config import StrategyParameters
from emporos.strategies.registry import (
    DuplicateStrategyError,
    StrategyRegistry,
    UnknownStrategyError,
)
from tests.support.strategies import (
    ScriptedStrategy,
    ThresholdParameters,
    ThresholdStrategy,
    make_config,
)


class _Second(ScriptedStrategy):
    name = "second"


def test_strategies_register_and_resolve_by_name() -> None:
    registry = StrategyRegistry()
    registry.register(ThresholdStrategy)
    registry.register(_Second)

    assert registry.get("threshold") is ThresholdStrategy
    assert registry.names() == ("second", "threshold")
    assert registry.parameters_model("threshold") is ThresholdParameters


def test_two_registries_are_independent() -> None:
    first, second = StrategyRegistry(), StrategyRegistry()
    first.register(ThresholdStrategy)
    assert second.names() == ()


def test_register_all_takes_any_iterable() -> None:
    registry = StrategyRegistry()
    registry.register_all(iter([ThresholdStrategy, _Second]))
    assert len(registry.names()) == 2


def test_a_name_cannot_be_taken_twice() -> None:
    registry = StrategyRegistry()
    registry.register(ThresholdStrategy)
    with pytest.raises(DuplicateStrategyError, match="ThresholdStrategy"):
        registry.register(ThresholdStrategy)


def test_an_unknown_name_says_what_is_registered() -> None:
    registry = StrategyRegistry()
    registry.register(ThresholdStrategy)
    with pytest.raises(UnknownStrategyError, match="threshold"):
        registry.get("nonesuch")
    with pytest.raises(LookupError):
        registry.parameters_model("nonesuch")


def test_only_concrete_strategies_can_be_registered() -> None:
    registry = StrategyRegistry()
    with pytest.raises(TypeError, match="Strategy subclass"):
        registry.register(dict)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="abstract"):
        registry.register(Strategy)


@pytest.mark.parametrize("bad", ["Bad Name", "UPPER", "1st", "has-dash", ""])
def test_a_strategy_needs_a_well_formed_name(bad: str) -> None:
    class BadName(ScriptedStrategy):
        name = bad

    with pytest.raises(ValueError, match="lowercase identifier"):
        StrategyRegistry().register(BadName)


def test_a_strategy_without_a_parameter_schema_is_refused() -> None:
    class Bare(Strategy):
        name = "bare"

        def initialize(self, ctx: object) -> None: ...  # type: ignore[override]
        def on_market_data(self, event: object) -> None: ...  # type: ignore[override]
        def generate_signal(self) -> None: ...

    with pytest.raises(ValueError, match="parameters_model"):
        StrategyRegistry().register(Bare)


def test_create_builds_the_named_strategy_from_its_config() -> None:
    registry = StrategyRegistry()
    registry.register(ScriptedStrategy)

    strategy = registry.create(make_config(name="scripted"))

    assert isinstance(strategy, ScriptedStrategy)


def test_create_refuses_parameters_of_the_wrong_kind() -> None:
    registry = StrategyRegistry()
    registry.register(ThresholdStrategy)
    config = make_config(name="threshold", parameters=StrategyParameters())

    with pytest.raises(TypeError, match="ThresholdParameters"):
        registry.create(config)


def test_a_strategy_refuses_another_strategys_config() -> None:
    with pytest.raises(ValueError, match="cannot run"):
        ScriptedStrategy(make_config(name="threshold"))
