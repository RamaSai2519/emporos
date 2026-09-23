"""The benchmark and the verdict's thresholds, loaded from `config/robustness/benchmark.yaml`.

One validated, immutable document. Numbers are exact (integers or quoted strings, never a YAML
float) and unknown keys are refused, so a mistyped threshold cannot silently leave a check
unenforced. `BenchmarkScaler` derives the risk limits and strategy sizing a run at the benchmark's
capital needs from the platform's own limits, changing only what depends on capital.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from emporos.core.config import CONFIG_DIR
from emporos.core.errors import ConfigurationError
from emporos.risk.limits import RiskLimits
from emporos.strategies.config import ExactDecimal, PositiveInt, ResolvedStrategyConfig

DEFAULT_BENCHMARK_FILE = CONFIG_DIR / "robustness" / "benchmark.yaml"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CostScenario(_Frozen):
    name: str = Field(min_length=1)
    fee_multiplier: ExactDecimal = Field(gt=0)
    extra_slippage_bps: ExactDecimal  # per side, on top of the benchmark's own slippage


class MonteCarloSettings(_Frozen):
    seed: int
    resamples: PositiveInt
    min_trades: PositiveInt


class ConcentrationLimits(_Frozen):
    max_top_instrument_share: ExactDecimal = Field(gt=0)
    max_top_month_share: ExactDecimal = Field(gt=0)
    top_trades: PositiveInt
    max_top_trades_share: ExactDecimal = Field(gt=0)


class PerturbationLimits(_Frozen):
    min_profitable_neighbour_share: ExactDecimal = Field(gt=0, le=1)


class VerdictThresholds(_Frozen):
    confidence: ExactDecimal = Field(gt=0, lt=1)
    monte_carlo: MonteCarloSettings
    min_probability_net_positive: ExactDecimal = Field(gt=0, le=1)
    max_probability_net_positive_to_reject: ExactDecimal = Field(ge=0, lt=1)
    min_trades_to_validate: PositiveInt
    min_history_days: PositiveInt
    min_windows: PositiveInt
    min_positive_window_share: ExactDecimal = Field(gt=0, le=1)
    max_window_drawdown: ExactDecimal = Field(gt=0, le=1)
    min_deflated_sharpe: ExactDecimal = Field(gt=0, lt=1)
    # 1 disables the check (no benchmark.yaml has to add this to stay valid): PBO is the
    # probability that a parameter search's in-sample winner was an out-of-sample loser
    # (EM-182's CSCV), and a threshold below 1 rejects a candidate whose overfitting evidence is
    # too strong to ignore rather than merely leaving it unproven.
    max_pbo: ExactDecimal = Field(ge=0, le=1, default=Decimal(1))
    # EM-183: the observed average gross edge per trade must clear this many times the modeled
    # minimum edge (brokerage + statutory charges + spread + slippage, at whatever quantity each
    # trade actually used) before it counts as economically real. 1.5, not 1, because the point
    # is surviving PLAUSIBLE ERROR in the cost model itself, not merely breaking even against it.
    min_edge_safety_margin: ExactDecimal = Field(ge=1, default=Decimal("1.5"))
    concentration: ConcentrationLimits
    perturbation: PerturbationLimits
    # EM-184: no default — every benchmark file must make an explicit, deliberate choice rather
    # than silently inheriting "no regime-diversity requirement" (the historical default before
    # this file was hardened). A benchmark that genuinely wants no requirement still sets this to
    # 1 (the gate treats <=1 as an explicit, visible opt-out, not a value nobody chose);
    # `RegimeDiversity` exempts a strategy declared `regime_specific` regardless of this number.
    min_regimes: PositiveInt

    @model_validator(mode="after")
    def _reject_below_validate(self) -> VerdictThresholds:
        if self.max_probability_net_positive_to_reject >= self.min_probability_net_positive:
            raise ValueError("the rejection line must sit below the line for calling a profit real")
        return self


class BenchmarkConfig(_Frozen):
    capital: ExactDecimal = Field(gt=0)
    max_position_fraction: ExactDecimal = Field(gt=0, le=1)
    max_daily_loss_fraction: ExactDecimal = Field(gt=0, le=1)
    slippage_bps: ExactDecimal = Field(ge=0)
    # EM-183: separate from slippage_bps (market impact) so a report can show which of the two is
    # doing the damage; 0 by default (a benchmark file opts in by naming its own assumption).
    spread_bps: ExactDecimal = Field(ge=0, default=Decimal(0))
    cost_scenarios: tuple[CostScenario, ...] = Field(min_length=1)
    adverse_scenario: str
    verdict: VerdictThresholds

    @model_validator(mode="after")
    def _scenarios_are_sound(self) -> BenchmarkConfig:
        names = [s.name for s in self.cost_scenarios]
        if len(set(names)) != len(names):
            raise ValueError("a cost scenario is named twice")
        if self.adverse_scenario not in names:
            raise ValueError(f"the adverse scenario '{self.adverse_scenario}' is not declared")
        if any(s.extra_slippage_bps < -self.slippage_bps for s in self.cost_scenarios):
            raise ValueError("a scenario cannot remove more slippage than the benchmark has")
        return self

    def scenario(self, name: str) -> CostScenario:
        return next(s for s in self.cost_scenarios if s.name == name)

    @property
    def max_position_value(self) -> Decimal:
        return self.capital * self.max_position_fraction

    @property
    def max_daily_loss(self) -> Decimal:
        return self.capital * self.max_daily_loss_fraction


class BenchmarkLoader:
    def __init__(self, path: Path = DEFAULT_BENCHMARK_FILE) -> None:
        self._path = path

    def load(self) -> BenchmarkConfig:
        try:
            document = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"cannot read benchmark {self._path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{self._path} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{self._path} must be a mapping")
        try:
            return BenchmarkConfig.model_validate(document)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            raise ConfigurationError(f"invalid benchmark in {self._path}: {problems}") from error


class BenchmarkScaler:
    """Sizes the platform's limits and a strategy's positions to the benchmark's capital."""

    def __init__(self, benchmark: BenchmarkConfig) -> None:
        self._benchmark = benchmark

    def limits(self, base: RiskLimits) -> RiskLimits:
        b = self._benchmark
        return base.model_copy(
            update={
                "max_position_value": b.max_position_value,
                "max_daily_loss": b.max_daily_loss,
                "max_strategy_loss": min(base.max_strategy_loss, b.max_daily_loss),
                "max_capital_deployed": min(base.max_capital_deployed, b.capital),
            }
        )

    def strategy(self, config: ResolvedStrategyConfig) -> ResolvedStrategyConfig:
        risk = config.risk.model_copy(
            update={"max_position_value": self._benchmark.max_position_value}
        )
        return config.model_copy(update={"risk": risk})
