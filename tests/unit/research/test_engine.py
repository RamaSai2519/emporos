"""EM-178: `AlphaDiscoveryEngine` — pooled and regime-segmented evaluation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.unit.research.conftest import bar

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.fees import FeeSchedule
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.research.costs import TransactionCostModel
from emporos.research.engine import AlphaDiscoveryEngine
from emporos.research.features import CausalHistory
from emporos.research.horizons import ForwardReturnCalculator

SCHEDULE = FeeSchedule(
    name="test",
    effective_from=date(2026, 1, 1),
    brokerage_flat=Money.of("20"),
    brokerage_percent=Decimal("0.1"),
    brokerage_minimum=Money.of("5"),
    stt_sell_percent=Decimal("0.025"),
    exchange_transaction_percent={Exchange.NSE: Decimal("0.0030699")},
    sebi_per_crore=Money.of("10"),
    stamp_duty_buy_percent=Decimal("0.003"),
    gst_percent=Decimal("18"),
)
FREE = TransactionCostModel(SCHEDULE, slippage_bps=Decimal(0))


class AlwaysOn:
    """Fires on every bar: a trivial feature for exercising the pooled evaluation path."""

    def compute(self, history: CausalHistory) -> Decimal | None:
        return Decimal(1)


class EvenBarsOnly:
    """Defined (non-None) only every other bar, to check that `None` readings are excluded from
    the observation set rather than treated as a feature value of zero."""

    def compute(self, history: CausalHistory) -> Decimal | None:
        return Decimal(1) if len(history) % 2 == 0 else None


class AlternatingAxis:
    """A fake regime axis: "even"/"odd" by bar position, deterministic and stateless."""

    name = "fake"

    def __init__(self) -> None:
        self._count = -1

    def update(self, candle: Candle) -> str | None:
        self._count += 1
        return "even" if self._count % 2 == 0 else "odd"


def uptrend(n: int) -> list[Candle]:
    return [bar(i, 100 + i) for i in range(n)]


def engine(
    cost_model: TransactionCostModel = FREE, regime_axes: list[type] | None = None
) -> AlphaDiscoveryEngine:
    return AlphaDiscoveryEngine(
        ForwardReturnCalculator(Timeframe.M5), cost_model, Exchange.NSE, 100,
        regime_axes=regime_axes or [],
    )  # fmt: skip


def test_evaluate_produces_a_pooled_segment_for_every_horizon() -> None:
    segments = engine().evaluate(AlwaysOn(), uptrend(20))

    pooled = [s for s in segments if s.axis is None]
    assert len(pooled) == 4  # 5m, 15m, 30m, 60m
    assert all(s.report.sample_size > 0 for s in pooled)


def test_an_uptrend_produces_a_positive_conditional_expectancy() -> None:
    segments = engine().evaluate(AlwaysOn(), uptrend(20))

    pooled_5m = next(s for s in segments if s.axis is None and s.horizon.label == "5m")
    assert pooled_5m.report.conditional_expectancy is not None
    assert pooled_5m.report.conditional_expectancy > Decimal(0)


def test_cost_adjusted_expectancy_is_never_higher_than_the_raw_expectancy() -> None:
    costly = TransactionCostModel(SCHEDULE, slippage_bps=Decimal(50))

    segments = engine(cost_model=costly).evaluate(AlwaysOn(), uptrend(20))

    pooled_5m = next(s for s in segments if s.axis is None and s.horizon.label == "5m")
    assert pooled_5m.report.cost_adjusted_expectancy is not None
    assert pooled_5m.report.conditional_expectancy is not None
    assert pooled_5m.report.cost_adjusted_expectancy < pooled_5m.report.conditional_expectancy


def test_a_feature_that_is_sometimes_none_only_contributes_when_defined() -> None:
    always_on_segments = engine().evaluate(AlwaysOn(), uptrend(20))
    sometimes_segments = engine().evaluate(EvenBarsOnly(), uptrend(20))

    always_on_5m = next(s for s in always_on_segments if s.axis is None and s.horizon.label == "5m")
    sometimes_5m = next(s for s in sometimes_segments if s.axis is None and s.horizon.label == "5m")
    assert sometimes_5m.report.sample_size < always_on_5m.report.sample_size


def test_regime_segments_are_produced_per_axis_and_bucket() -> None:
    segments = engine(regime_axes=[AlternatingAxis]).evaluate(AlwaysOn(), uptrend(20))

    fake_segments = [s for s in segments if s.axis == "fake"]
    assert {s.label for s in fake_segments} == {"even", "odd"}
    assert all(s.report.sample_size > 0 for s in fake_segments)


def test_the_cost_model_label_is_exposed_for_the_ledger() -> None:
    assert engine().cost_model_label == FREE.label


def test_quantity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="quantity"):
        AlphaDiscoveryEngine(ForwardReturnCalculator(Timeframe.M5), FREE, Exchange.NSE, 0)
