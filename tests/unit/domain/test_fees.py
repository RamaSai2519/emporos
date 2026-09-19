"""Intraday charges: hand-computed against the schedule, component by component."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from emporos.domain.fees import FeeSchedule, IntradayCharges
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

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
CHARGES = IntradayCharges(SCHEDULE)


def test_a_buy_pays_stamp_duty_and_no_stt() -> None:
    c = CHARGES.for_trade(Exchange.NSE, OrderSide.BUY, 100, Money.of("500"))  # turnover 50,000

    assert (c.brokerage, c.stt, c.exchange_transaction, c.sebi, c.stamp_duty, c.gst) == (
        Money.of("20.00"), Money.of("0.00"), Money.of("1.53"),
        Money.of("0.05"), Money.of("1.50"), Money.of("3.88"),
    )  # fmt: skip
    assert c.total == Money.of("26.96")


def test_a_sell_pays_stt_and_no_stamp_duty() -> None:
    c = CHARGES.for_trade(Exchange.NSE, OrderSide.SELL, 100, Money.of("510"))  # turnover 51,000

    assert (c.stt, c.stamp_duty, c.exchange_transaction, c.gst) == (
        Money.of("12.75"), Money.of("0.00"), Money.of("1.57"), Money.of("3.89"),
    )  # fmt: skip
    assert c.total == Money.of("38.26")


def test_brokerage_is_the_lower_of_cap_and_percentage_but_never_below_the_minimum() -> None:
    tiny = CHARGES.for_trade(Exchange.NSE, OrderSide.BUY, 1, Money.of("100"))  # 0.1% = 0.10
    mid = CHARGES.for_trade(Exchange.NSE, OrderSide.BUY, 10, Money.of("1000"))  # 0.1% = 10
    big = CHARGES.for_trade(Exchange.NSE, OrderSide.BUY, 1000, Money.of("1000"))  # 0.1% = 1000

    assert (tiny.brokerage, mid.brokerage, big.brokerage) == (
        Money.of("5.00"), Money.of("10.00"), Money.of("20.00"),
    )  # fmt: skip
    assert tiny.gst == Money.of("0.90")  # GST is on brokerage + fees, and the fees round to nothing


def test_components_round_half_up_to_the_paisa() -> None:
    c = CHARGES.for_trade(Exchange.NSE, OrderSide.SELL, 1, Money.of("100.10"))  # 0.025% = 0.025025

    assert c.stt == Money.of("0.03")


def test_a_trade_on_an_exchange_the_schedule_does_not_price_is_refused() -> None:
    with pytest.raises(ValueError, match="BSE"):
        CHARGES.for_trade(Exchange.BSE, OrderSide.BUY, 1, Money.of("100"))
    with pytest.raises(ValueError, match="positive"):
        CHARGES.for_trade(Exchange.NSE, OrderSide.BUY, 0, Money.of("100"))


def test_a_schedule_with_a_negative_or_missing_rate_is_refused() -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="negative"):
        replace(SCHEDULE, gst_percent=Decimal("-1"))
    with pytest.raises(ValueError, match="negative"):
        replace(SCHEDULE, brokerage_minimum=Money.of("-1"))
    with pytest.raises(ValueError, match="at least one"):
        replace(SCHEDULE, exchange_transaction_percent={})
