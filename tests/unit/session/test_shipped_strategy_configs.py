"""Every file in `config/strategies/` must load: a broken shipped config is caught in CI, not at
the open of a trading session."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from emporos.cli.strategy_composition import build_registry
from emporos.core.config import CONFIG_DIR
from emporos.domain.candles import Timeframe
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.instruments.cache import InstrumentCache
from emporos.session.strategy_files import STRATEGY_CONFIG_DIR, StrategyConfigLoader
from emporos.strategies.builtin.momentum_v1 import MomentumParameters
from emporos.strategies.resolution import StrategyConfigResolver

MASTER = InstrumentCache(
    [
        Instrument(Exchange.NSE, "1001", "RELIANCE-EQ", "Reliance", 1, Money.of("0.05")),
        Instrument(Exchange.NSE, "1002", "TCS-EQ", "TCS", 1, Money.of("0.05")),
    ]
)


def _loader() -> StrategyConfigLoader:
    return StrategyConfigLoader(StrategyConfigResolver(build_registry(), MASTER))


def _has_float(node: object) -> bool:
    if isinstance(node, float):
        return True
    if isinstance(node, dict):
        return any(_has_float(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_float(v) for v in node)
    return False


def test_every_shipped_strategy_file_loads_and_validates() -> None:
    assert list(STRATEGY_CONFIG_DIR.glob("*.yaml")), "the loader would pass vacuously"
    assert {c.name for c in _loader().load_all()} >= {"momentum_v1"}


def test_no_shipped_strategy_file_contains_a_yaml_float() -> None:
    for path in STRATEGY_CONFIG_DIR.glob("*.yaml"):
        assert not _has_float(yaml.safe_load(path.read_text())), path.name


def test_momentum_v1_is_the_plan_9_example() -> None:
    config = {c.name: c for c in _loader().load_all()}["momentum_v1"]

    assert config.enabled and config.timeframe is Timeframe.M5
    assert [m.symbol for m in config.universe] == ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"]
    assert isinstance(config.parameters, MomentumParameters)
    assert (config.parameters.fast_ema, config.parameters.slow_ema) == (20, 50)
    assert config.parameters.rsi_period == 14
    assert config.risk.max_position_value == Decimal(50000) and config.risk.max_open_positions == 3
    assert (config.risk.stop_loss_pct, config.risk.target_pct) == (Decimal("1.0"), Decimal("2.0"))
    assert config.execution.limit_buffer_bps == 5 and config.execution.max_reprices == 3
    assert config.session.no_new_entries_after.isoformat() == "15:00:00"
    assert config.session.square_off_at.isoformat() == "15:15:00"


def test_the_config_directory_is_the_one_the_loader_reads() -> None:
    assert STRATEGY_CONFIG_DIR == CONFIG_DIR / "strategies" == Path(STRATEGY_CONFIG_DIR)
