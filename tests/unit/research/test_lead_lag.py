"""EM-180: `LeadLagEngine` — early-window predictor vs subsequent/late-session target returns."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from tests.unit.research.conftest import bar

from emporos.domain.candles import Candle
from emporos.domain.fees import FeeSchedule
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.sizing import DeclaredSize
from emporos.research.costs import TransactionCostModel
from emporos.research.horizons import Horizon
from emporos.research.lead_lag import LATE_SESSION, LeadLagEngine

D = Decimal
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
EARLY = Horizon(timedelta(minutes=10), bars=2)
TARGET = Horizon(timedelta(minutes=10), bars=2)

DAY1, DAY2, DAY3 = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)


def _bars() -> list[Candle]:
    return [bar(i, 100 + i) for i in range(4)]


def _engine(**overrides: object) -> LeadLagEngine:
    defaults: dict[str, object] = dict(
        early_horizons=[EARLY],
        target_horizons=[TARGET],
        cost_model=FREE,
        exchange=Exchange.NSE,
        size=DeclaredSize(Decimal(50_000)),
    )
    defaults.update(overrides)
    return LeadLagEngine(**defaults)  # type: ignore[arg-type]


def _series() -> (
    tuple[dict[date, list[Decimal]], dict[date, list[Decimal]], dict[date, list[Candle]]]
):
    predictor_by_day = {
        DAY1: [D("0.01"), D("0.01")],
        DAY2: [D("-0.01"), D("-0.01")],
        DAY3: [D("0.02"), D("0.02")],
    }
    target_by_day = {
        DAY1: [D("0.005"), D("0.005"), D("0.02"), D("0.02")],
        DAY2: [D("-0.005"), D("-0.005"), D("-0.02"), D("-0.02")],
        DAY3: [D("0.01"), D("0.01"), D("0.03"), D("0.03")],
    }
    regime_bars_by_day = {DAY1: _bars(), DAY2: _bars(), DAY3: _bars()}
    return predictor_by_day, target_by_day, regime_bars_by_day


def test_at_least_one_early_horizon_is_required() -> None:
    with pytest.raises(ValueError, match="early horizon"):
        _engine(early_horizons=[])


def test_the_declared_size_is_exposed_for_the_report() -> None:
    assert _engine().size == DeclaredSize(Decimal(50_000))


def test_cost_model_label_is_exposed() -> None:
    assert _engine().cost_model_label == FREE.label


def test_up_days_and_down_days_both_show_continuation() -> None:
    predictor_by_day, target_by_day, regime_bars_by_day = _series()

    segments = _engine().evaluate(predictor_by_day, target_by_day, regime_bars_by_day)
    pooled = {(s.direction, s.target_horizon_label): s for s in segments if s.axis is None}

    up = pooled[("up", "10m")].report
    down = pooled[("down", "10m")].report
    assert up.sample_size == 2
    assert up.conditional_expectancy is not None and up.conditional_expectancy > D(0)
    assert down.sample_size == 1
    assert down.conditional_expectancy is not None and down.conditional_expectancy < D(0)


def test_late_session_target_is_included_by_default() -> None:
    predictor_by_day, target_by_day, regime_bars_by_day = _series()

    segments = _engine().evaluate(predictor_by_day, target_by_day, regime_bars_by_day)

    assert any(s.target_horizon_label == LATE_SESSION for s in segments)


def test_late_session_target_can_be_excluded() -> None:
    predictor_by_day, target_by_day, regime_bars_by_day = _series()
    engine = _engine(include_late_session=False)

    segments = engine.evaluate(predictor_by_day, target_by_day, regime_bars_by_day)

    assert not any(s.target_horizon_label == LATE_SESSION for s in segments)


def test_a_sector_level_target_with_no_price_reports_net_equal_to_gross() -> None:
    predictor_by_day, target_by_day, regime_bars_by_day = _series()

    segments = _engine().evaluate(predictor_by_day, target_by_day, regime_bars_by_day)
    pooled = {(s.direction, s.target_horizon_label): s.report for s in segments if s.axis is None}

    up = pooled[("up", "10m")]
    assert up.conditional_expectancy == up.cost_adjusted_expectancy


def test_a_stock_level_target_with_a_price_never_reports_net_above_gross() -> None:
    predictor_by_day, target_by_day, regime_bars_by_day = _series()
    price_by_day = {day: Money.of("100") for day in (DAY1, DAY2, DAY3)}
    costly = _engine(cost_model=TransactionCostModel(SCHEDULE, slippage_bps=Decimal(50)))

    segments = costly.evaluate(predictor_by_day, target_by_day, regime_bars_by_day, price_by_day)

    for segment in segments:
        net, gross = segment.report.cost_adjusted_expectancy, segment.report.conditional_expectancy
        if net is not None and gross is not None:
            assert net <= gross
