"""The pre-declared EM-114 plan is sound before any hours are spent running it: every candidate
overrides only parameters its strategy has, with values its own validation accepts."""

from pathlib import Path

import pytest
import yaml

from emporos.backtest.tuning import ConfigVariants, ParameterCandidate
from tests.unit.session.test_shipped_strategy_configs import _loader

PLAN = yaml.safe_load(Path("config/curation/plan_em114.yaml").read_text())
ORIGINAL = yaml.safe_load(Path("config/curation/plan.yaml").read_text())


def test_it_is_the_same_protocol_as_the_first_plan() -> None:
    assert PLAN["objective"] == ORIGINAL["objective"] == "sharpe"
    assert PLAN["windows"] == ORIGINAL["windows"]


def test_the_five_strategies_are_each_declared_once_with_six_named_candidates() -> None:
    names = [s["name"] for s in PLAN["strategies"]]

    assert names == ["vwap_trend_v1", "donchian_v1", "ema_pullback_v1", "gap_go_v1", "gap_fade_v1"]
    for strategy in PLAN["strategies"]:
        candidates = [c["name"] for c in strategy["candidates"]]
        assert len(candidates) == 6 and len(set(candidates)) == 6


@pytest.mark.parametrize("entry", PLAN["strategies"], ids=lambda e: e["name"])
def test_every_candidate_applies_to_its_strategy(entry: dict) -> None:  # type: ignore[type-arg]
    config = _loader().load_file(Path(entry["file"]))
    assert config.name == entry["name"] and config.enabled is False

    variants = [
        ConfigVariants().apply(config, ParameterCandidate(c["name"], c["overrides"]))
        for c in entry["candidates"]
    ]

    assert len({v.parameters.model_dump_json() for v in variants}) == 6  # six different settings


def test_no_new_strategy_is_enabled() -> None:
    assert [c.name for c in _loader().load_enabled()] == []
