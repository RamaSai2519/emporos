"""The per-trade risk guard (EM-189): |limit - protective stop| x quantity against a cap."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from emporos.risk.limits import RiskLimits
from emporos.risk.rules.exposure import MaxRiskPerTradeGuard
from emporos.risk.standard import StandardRuleSet
from tests.support.risk import healthy
from tests.support.strategies import make_signal

GUARD = MaxRiskPerTradeGuard(Decimal("150"))


def entry(stop: str | None, side: OrderSide = OrderSide.BUY, quantity: int = 10):  # type: ignore[no-untyped-def]
    return replace(
        make_signal(side=side, quantity=quantity, price="100"),
        protective_stop=None if stop is None else Money.of(stop),
    )


def test_an_entry_whose_risk_is_exactly_the_cap_is_allowed() -> None:
    assert GUARD.evaluate(entry("85"), healthy()).allowed  # 15 x 10 = 150


def test_one_paisa_of_risk_over_the_cap_is_blocked() -> None:
    verdict = GUARD.evaluate(entry("84.99"), healthy())
    assert not verdict.allowed and "exceeds the per-trade cap" in verdict.reason


def test_a_short_entry_is_measured_from_a_stop_above_the_limit() -> None:
    assert GUARD.evaluate(entry("115", OrderSide.SELL), healthy()).allowed
    assert not GUARD.evaluate(entry("115.01", OrderSide.SELL), healthy()).allowed


def test_an_entry_with_no_stop_is_blocked_because_its_risk_is_unknown() -> None:
    verdict = GUARD.evaluate(entry(None), healthy())
    assert not verdict.allowed and "no protective stop" in verdict.reason


def test_an_exit_is_never_measured() -> None:
    exit_signal = make_signal(kind=SignalKind.EXIT, side=OrderSide.SELL)
    assert GUARD.evaluate(exit_signal, healthy()).allowed


def test_the_cap_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        MaxRiskPerTradeGuard(Decimal(0))


def _limits(**changes: object) -> RiskLimits:
    base = RiskLimits(
        max_daily_loss=Decimal(1000), max_strategy_loss=Decimal(1000),
        max_position_value=Decimal(5000), max_open_positions=2,
        max_capital_deployed=Decimal(10000), max_order_quantity=500,
        max_price_deviation_pct=Decimal(2), max_spread_bps=Decimal(20),
        duplicate_window_seconds=5, max_orders_per_second=2, max_orders_per_minute=60,
    )  # fmt: skip
    return base.model_copy(update=changes)


def _names(limits: RiskLimits) -> list[str]:
    from datetime import time

    from emporos.marketdata.session import SessionWindow

    window = SessionWindow(time(9, 15), time(15, 30))
    return [r.name for r in StandardRuleSet(limits, window).rules()]


def test_the_rule_set_registers_the_guard_only_when_the_cap_is_configured() -> None:
    assert "MaxRiskPerTradeGuard" not in _names(_limits())
    names = _names(_limits(max_risk_per_trade=Decimal(150)))
    assert names.index("MaxRiskPerTradeGuard") == names.index("MaxPositionValueGuard") + 1
