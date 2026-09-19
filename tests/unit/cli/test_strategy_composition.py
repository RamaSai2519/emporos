from __future__ import annotations

import tests.support.strategy_pack as pack
from emporos.cli.strategy_composition import StrategyDiscovery, build_registry
from tests.support.strategy_pack.alpha import AlphaStrategy
from tests.support.strategy_pack.beta import BetaStrategy


def test_discovery_finds_the_concrete_strategies_defined_in_a_package() -> None:
    found = StrategyDiscovery().discover(pack)

    assert found == [AlphaStrategy, BetaStrategy]  # by name; no re-exports, abstracts or strangers


def test_discovery_is_repeatable() -> None:
    assert StrategyDiscovery().discover(pack) == StrategyDiscovery().discover(pack)


def test_the_registry_is_built_from_whatever_the_package_holds() -> None:
    registry = build_registry(pack)

    assert registry.names() == ("alpha", "beta")
    assert registry.get("alpha") is AlphaStrategy


def test_each_call_builds_a_fresh_registry() -> None:
    assert build_registry(pack) is not build_registry(pack)
