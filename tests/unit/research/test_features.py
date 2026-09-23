"""EM-178: `CausalHistory`, `FeatureSeries` and `FeatureRegistry`."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.unit.research.conftest import bars

from emporos.research.features import (
    CausalHistory,
    DuplicateFeatureError,
    Feature,
    FeatureDefinition,
    FeatureRegistry,
    FeatureSeries,
)


class LastClose:
    """A trivial feature: the close of the bar being evaluated."""

    def compute(self, history: CausalHistory) -> Decimal | None:
        return history.last.close.amount


class CheatsByReadingAhead:
    """A feature that tries to read one bar past the one it was given — must be refused."""

    def compute(self, history: CausalHistory) -> Decimal | None:
        return history[len(history)].close.amount  # one past the last valid index


def test_causal_history_exposes_only_up_to_the_given_index() -> None:
    series = bars([10, 20, 30])

    view = CausalHistory(series, 1)

    assert len(view) == 2
    assert view[0].close.amount == 10
    assert view[1].close.amount == 20
    assert view.last.close.amount == 20
    with pytest.raises(IndexError):
        _ = view[2]


def test_causal_history_supports_negative_indexing_and_slicing() -> None:
    view = CausalHistory(bars([10, 20, 30]), 2)

    assert view[-1].close.amount == 30
    assert [c.close.amount for c in view[:2]] == [10, 20]


def test_a_feature_that_reads_past_its_index_is_refused() -> None:
    series = bars([10, 20, 30])

    with pytest.raises(IndexError):
        CheatsByReadingAhead().compute(CausalHistory(series, 1))


def test_feature_series_computes_one_value_per_bar_causally() -> None:
    series = bars([10, 20, 30])

    values = FeatureSeries(LastClose()).compute(series)

    assert values == [Decimal(10), Decimal(20), Decimal(30)]


def test_feature_definition_hashes_by_name_version_and_parameters() -> None:
    a = FeatureDefinition("momentum", "v1", "n-bar return", {"lookback": 5})
    b = FeatureDefinition("momentum", "v1", "n-bar return", {"lookback": 5})
    c = FeatureDefinition("momentum", "v1", "n-bar return", {"lookback": 10})

    assert a.content_hash == b.content_hash
    assert a.content_hash != c.content_hash
    assert a.key == "momentum@v1"


def test_a_feature_definition_needs_a_name_and_version() -> None:
    with pytest.raises(ValueError, match="name"):
        FeatureDefinition("", "v1", "", {})
    with pytest.raises(ValueError, match="version"):
        FeatureDefinition("momentum", "", "", {})


def test_the_registry_refuses_to_register_the_same_key_twice() -> None:
    registry = FeatureRegistry()
    definition = FeatureDefinition("momentum", "v1", "", {})
    registry.register(definition, LastClose())

    with pytest.raises(DuplicateFeatureError):
        registry.register(definition, LastClose())


def test_the_registry_resolves_by_name_and_version() -> None:
    registry = FeatureRegistry()
    definition = FeatureDefinition("momentum", "v1", "", {})
    feature: Feature = LastClose()
    registry.register(definition, feature)

    found_definition, found_feature = registry.get("momentum", "v1")

    assert found_definition == definition
    assert found_feature is feature
    assert registry.all() == (definition,)


def test_the_registry_raises_a_clear_error_for_an_unknown_feature() -> None:
    with pytest.raises(KeyError, match="unknown@v9"):
        FeatureRegistry().get("unknown", "v9")
