"""EM-222: delivery charges, hand-computed component by component, and the product guard."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from emporos.domain.fees import DeliveryCharges, FeeSchedule, IntradayCharges, TradeProduct
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

DELIVERY = FeeSchedule(
    name="test-delivery",
    effective_from=date(2026, 1, 1),
    brokerage_flat=Money.of("20"),
    brokerage_percent=Decimal("0.1"),
    brokerage_minimum=Money.of("5"),
    stt_sell_percent=Decimal("0.1"),
    exchange_transaction_percent={Exchange.NSE: Decimal("0.0030699")},
    sebi_per_crore=Money.of("10"),
    stamp_duty_buy_percent=Decimal("0.015"),
    gst_percent=Decimal("18"),
    product=TradeProduct.DELIVERY,
    stt_buy_percent=Decimal("0.1"),
    dp_charge_per_sale=Money.of("20"),
)
CHARGES = DeliveryCharges(DELIVERY)


def test_a_buy_pays_stt_and_the_higher_stamp_duty_and_no_dp() -> None:
    c = CHARGES.for_trade(Exchange.NSE, OrderSide.BUY, 100, Money.of("500"))  # turnover 50,000

    assert (c.brokerage, c.stt, c.exchange_transaction, c.sebi, c.stamp_duty, c.dp, c.gst) == (
        Money.of("20.00"), Money.of("50.00"), Money.of("1.53"),
        Money.of("0.05"), Money.of("7.50"), Money.of("0.00"), Money.of("3.88"),
    )  # fmt: skip
    assert c.total == Money.of("82.96")


def test_a_sell_pays_stt_and_the_dp_charge_with_gst_on_it() -> None:
    c = CHARGES.for_trade(Exchange.NSE, OrderSide.SELL, 100, Money.of("510"))  # turnover 51,000

    assert (c.stt, c.stamp_duty, c.dp, c.exchange_transaction) == (
        Money.of("51.00"), Money.of("0.00"), Money.of("20.00"), Money.of("1.57"),
    )  # fmt: skip
    assert c.gst == Money.of("7.49")  # 18% of 20 + 1.57 + 0.05 + the 20 DP charge
    assert c.total == Money.of("100.11")


def test_a_small_delivery_order_still_pays_the_dp_charge_on_the_sale() -> None:
    c = CHARGES.for_trade(Exchange.NSE, OrderSide.SELL, 10, Money.of("100"))  # turnover 1,000

    assert c.dp == Money.of("20.00")
    assert c.brokerage == Money.of("5.00")  # 0.1% is 1.00: the minimum applies


def test_a_delivery_round_trip_costs_more_than_an_intraday_one_of_the_same_size() -> None:
    intraday = replace(
        DELIVERY,
        product=TradeProduct.INTRADAY,
        stt_sell_percent=Decimal("0.025"),
        stt_buy_percent=Decimal(0),
        dp_charge_per_sale=Money.zero(),
        stamp_duty_buy_percent=Decimal("0.003"),
    )

    def round_trip(charges: DeliveryCharges | IntradayCharges) -> Money:
        buy = charges.for_trade(Exchange.NSE, OrderSide.BUY, 100, Money.of("500"))
        sell = charges.for_trade(Exchange.NSE, OrderSide.SELL, 100, Money.of("500"))
        return buy.total + sell.total

    assert round_trip(CHARGES) > round_trip(IntradayCharges(intraday))


def test_a_calculator_refuses_a_schedule_of_the_other_product() -> None:
    with pytest.raises(ValueError, match="needs a schedule for delivery"):
        DeliveryCharges(replace(DELIVERY, product=TradeProduct.INTRADAY, stt_buy_percent=Decimal(0),
                                dp_charge_per_sale=Money.zero()))  # fmt: skip
    with pytest.raises(ValueError, match="needs a schedule for intraday"):
        IntradayCharges(DELIVERY)


def test_an_intraday_schedule_cannot_carry_delivery_only_charges() -> None:
    with pytest.raises(ValueError, match="no STT on the buy and no DP charge"):
        replace(DELIVERY, product=TradeProduct.INTRADAY)


def test_a_negative_dp_charge_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        replace(DELIVERY, dp_charge_per_sale=Money.of("-1"))
