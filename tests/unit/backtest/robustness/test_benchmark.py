"""The shipped benchmark loads, refuses what is unsound, and sizes the limits to its capital."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from emporos.backtest.robustness.benchmark import (
    DEFAULT_BENCHMARK_FILE,
    BenchmarkConfig,
    BenchmarkLoader,
    BenchmarkScaler,
)
from emporos.core.errors import ConfigurationError
from emporos.risk.config import RiskLimitsLoader

D = Decimal


def shipped() -> dict[str, object]:
    return yaml.safe_load(DEFAULT_BENCHMARK_FILE.read_text())  # type: ignore[no-any-return]


def build(document: dict[str, object]) -> BenchmarkConfig:
    return BenchmarkConfig.model_validate(document)


class TestShippedFile:
    def test_states_the_ticket_benchmark(self) -> None:
        b = BenchmarkLoader().load()

        assert (b.capital, b.max_position_fraction, b.max_daily_loss_fraction) == (
            D(100000), D("0.10"), D("0.02"),
        )  # fmt: skip
        assert b.slippage_bps == D(5)
        assert (b.max_position_value, b.max_daily_loss) == (D(10000), D(2000))

    def test_prices_the_deflated_sharpe_at_a_skill_less_sharpes_spread(self) -> None:
        assert BenchmarkLoader().load().verdict.deflated_sharpe_spread == "null_hypothesis"

    def test_covers_lower_and_higher_slippage_and_dearer_fees(self) -> None:
        b = BenchmarkLoader().load()

        extras = {s.extra_slippage_bps for s in b.cost_scenarios}
        assert min(extras) == -b.slippage_bps  # no slippage at all
        assert max(extras) > 0
        assert max(s.fee_multiplier for s in b.cost_scenarios) >= 2
        assert b.scenario(b.adverse_scenario).fee_multiplier > 1

    def test_the_rejection_line_is_below_the_line_for_calling_a_profit_real(self) -> None:
        v = BenchmarkLoader().load().verdict

        assert v.max_probability_net_positive_to_reject < v.min_probability_net_positive


class TestRefusals:
    def test_a_yaml_float_is_refused(self) -> None:
        document = shipped() | {"max_position_fraction": 0.1}

        with pytest.raises(ValueError):
            build(document)

    def test_an_unknown_key_is_refused(self) -> None:
        with pytest.raises(ValueError):
            build(shipped() | {"max_position_fractoin": "0.1"})

    def test_an_undeclared_adverse_scenario_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not declared"):
            build(shipped() | {"adverse_scenario": "nope"})

    def test_a_scenario_cannot_remove_more_slippage_than_exists(self) -> None:
        scenarios = [{"name": "s", "fee_multiplier": "1", "extra_slippage_bps": -6}]

        with pytest.raises(ValueError, match="remove more slippage"):
            build(shipped() | {"cost_scenarios": scenarios, "adverse_scenario": "s"})

    def test_a_scenario_named_twice_is_refused(self) -> None:
        one = {"name": "s", "fee_multiplier": "1", "extra_slippage_bps": 0}

        with pytest.raises(ValueError, match="twice"):
            build(shipped() | {"cost_scenarios": [one, one], "adverse_scenario": "s"})

    def test_a_rejection_line_at_or_above_the_profit_line_is_refused(self) -> None:
        verdict = dict(shipped()["verdict"]) | {"max_probability_net_positive_to_reject": "0.96"}  # type: ignore[call-overload]

        with pytest.raises(ValueError, match="rejection line"):
            build(shipped() | {"verdict": verdict})

    def test_an_unknown_deflated_sharpe_spread_is_refused_and_omitting_it_means_observed(
        self,
    ) -> None:
        verdict = dict(shipped()["verdict"])  # type: ignore[call-overload]

        with pytest.raises(ValueError, match="deflated_sharpe_spread"):
            build(shipped() | {"verdict": verdict | {"deflated_sharpe_spread": "lenient"}})
        del verdict["deflated_sharpe_spread"]
        assert build(shipped() | {"verdict": verdict}).verdict.deflated_sharpe_spread == "observed"

    def test_the_loader_reports_a_missing_or_broken_file_as_configuration_errors(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ConfigurationError, match="cannot read"):
            BenchmarkLoader(tmp_path / "nope.yaml").load()
        broken = tmp_path / "b.yaml"
        broken.write_text("capital: [")
        with pytest.raises(ConfigurationError, match="not valid YAML"):
            BenchmarkLoader(broken).load()
        broken.write_text("- a list")
        with pytest.raises(ConfigurationError, match="mapping"):
            BenchmarkLoader(broken).load()
        broken.write_text("capital: 0")
        with pytest.raises(ConfigurationError, match="invalid benchmark"):
            BenchmarkLoader(broken).load()


class TestScaler:
    def test_only_what_depends_on_capital_changes(self) -> None:
        base = RiskLimitsLoader().load()
        scaled = BenchmarkScaler(BenchmarkLoader().load()).limits(base)

        assert scaled.max_position_value == D(10000)
        assert scaled.max_daily_loss == D(2000)
        assert scaled.max_capital_deployed == D(100000)
        assert scaled.max_strategy_loss == D(2000)
        assert scaled.max_open_positions == base.max_open_positions
        assert scaled.max_order_quantity == base.max_order_quantity

    def test_it_never_loosens_a_limit_the_platform_already_has(self) -> None:
        base = (
            RiskLimitsLoader()
            .load()
            .model_copy(update={"max_strategy_loss": D(300), "max_capital_deployed": D(20000)})
        )

        scaled = BenchmarkScaler(BenchmarkLoader().load()).limits(base)

        assert scaled.max_strategy_loss == D(300)
        assert scaled.max_capital_deployed == D(20000)

    def test_a_declared_position_value_replaces_the_benchmarks_share_everywhere(self) -> None:
        from tests.unit.session.test_shipped_strategy_configs import _loader

        scaler = BenchmarkScaler(BenchmarkLoader().load(), D(25000))
        config = _loader().load_file(Path("config/strategies/orb_v1.yaml"))
        base = RiskLimitsLoader().load()

        assert scaler.strategy(config).risk.max_position_value == D(25000)
        assert scaler.limits(base).max_position_value == D(25000)
        # only the position size moved: the loss caps still follow the benchmark's capital
        assert scaler.limits(base).max_daily_loss == D(2000)

    def test_a_position_value_that_is_not_positive_is_refused(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            BenchmarkScaler(BenchmarkLoader().load(), D(0))

    def test_a_strategy_is_sized_to_the_benchmark_position(self) -> None:
        from tests.unit.session.test_shipped_strategy_configs import _loader

        config = _loader().load_file(Path("config/strategies/orb_v1.yaml"))

        sized = BenchmarkScaler(BenchmarkLoader().load()).strategy(config)

        assert config.risk.max_position_value == D(25000)
        assert sized.risk.max_position_value == D(10000)
        assert sized.parameters == config.parameters
