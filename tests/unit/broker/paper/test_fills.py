"""The fill policies: when the tape trades an order, at what price, and how many shares."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.broker.paper.fills import (
    AtLimitPrice,
    AtTradePrice,
    CappedLiquidity,
    FullLiquidity,
    LimitFillPolicy,
    ParticipationLiquidity,
    StopTrigger,
    ThroughCrossing,
    TouchCrossing,
    WorkingOrder,
)
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from tests.support.fakes import make_tick
from tests.support.paper_market import NOW

BUY = WorkingOrder(OrderSide.BUY, Money.of("100.00"), 10)
SELL = WorkingOrder(OrderSide.SELL, Money.of("100.00"), 10)


def tick(price: str):  # type: ignore[no-untyped-def]
    return make_tick(NOW, price)


@pytest.mark.parametrize(
    ("order", "price", "touch", "through"),
    [
        (BUY, "99.50", True, True),  # traded below a buy limit: through it
        (BUY, "100.00", True, False),  # traded exactly at it: touched, not through
        (BUY, "100.05", False, False),  # the wrong side of it: never
        (SELL, "100.50", True, True),
        (SELL, "100.00", True, False),
        (SELL, "99.95", False, False),
    ],
)
def test_crossing_rules_agree_on_what_is_through_and_differ_on_touching(
    order: WorkingOrder, price: str, touch: bool, through: bool
) -> None:
    assert TouchCrossing().crosses(order, Money.of(price)) is touch
    assert ThroughCrossing().crosses(order, Money.of(price)) is through


def test_a_price_on_the_wrong_side_never_fills_under_any_pricing() -> None:
    for pricing in (AtLimitPrice(), AtTradePrice()):
        policy = LimitFillPolicy(TouchCrossing(), pricing)
        assert policy.execution_price(BUY, tick("100.05")) is None
        assert policy.execution_price(SELL, tick("99.95")) is None


def test_the_conservative_price_is_the_orders_own_limit_never_an_improvement() -> None:
    policy = LimitFillPolicy(TouchCrossing(), AtLimitPrice())

    assert policy.execution_price(BUY, tick("99.00")) == Money.of("100.00")
    assert policy.execution_price(SELL, tick("101.00")) == Money.of("100.00")


def test_trade_price_fills_improve_slippage_erodes_and_the_limit_caps() -> None:
    plain = AtTradePrice()
    slipped = AtTradePrice(Money.of("0.30"))

    assert plain.price(BUY, Money.of("99.50")) == Money.of("99.50")
    assert slipped.price(BUY, Money.of("99.50")) == Money.of("99.80")  # 0.30 worse for a buyer
    assert slipped.price(SELL, Money.of("100.50")) == Money.of("100.20")  # 0.30 worse for a seller
    assert slipped.price(BUY, Money.of("99.90")) == Money.of(
        "100.00"
    )  # clamped: never above the limit
    assert slipped.price(SELL, Money.of("100.10")) == Money.of("100.00")


def test_slippage_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        AtTradePrice(Money.of("-0.05"))


def test_full_liquidity_is_unlimited() -> None:
    assert FullLiquidity().budget(0) is None
    assert FullLiquidity().budget(None) is None


def test_participation_takes_a_fraction_of_the_volume_that_traded() -> None:
    model = ParticipationLiquidity(Decimal("0.25"))

    assert model.budget(100) == 25
    assert model.budget(7) == 1  # rounded down: never more than the share
    assert model.budget(3) == 0


def test_without_a_volume_figure_participation_assumes_nothing_can_be_taken() -> None:
    assert ParticipationLiquidity(Decimal(1)).budget(None) == 0


@pytest.mark.parametrize("rate", ["0", "-0.1", "1.01"])
def test_participation_must_be_a_fraction(rate: str) -> None:
    with pytest.raises(ValueError, match="fraction"):
        ParticipationLiquidity(Decimal(rate))


def test_a_cap_limits_shares_per_tick_whatever_traded() -> None:
    assert CappedLiquidity(5).budget(1_000_000) == 5
    assert CappedLiquidity(5).budget(None) == 5
    with pytest.raises(ValueError, match="positive"):
        CappedLiquidity(0)


def test_a_stop_triggers_when_price_falls_to_a_sell_stop_or_rises_to_a_buy_stop() -> None:
    sell_stop = WorkingOrder(OrderSide.SELL, Money.of("99.00"), 10, Money.of("100.00"))
    buy_stop = WorkingOrder(OrderSide.BUY, Money.of("101.00"), 10, Money.of("100.00"))
    rule = StopTrigger()

    assert [rule.fires(sell_stop, tick(p)) for p in ("100.05", "100.00", "99.50")] == [
        False,
        True,
        True,
    ]
    assert [rule.fires(buy_stop, tick(p)) for p in ("99.95", "100.00", "100.50")] == [
        False,
        True,
        True,
    ]


def test_an_order_without_a_trigger_never_triggers() -> None:
    assert StopTrigger().fires(BUY, tick("1.00")) is False
