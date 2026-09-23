"""EM-178: `TransactionCostModel` — statutory round-trip charges plus a slippage assumption."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from emporos.domain.fees import FeeSchedule
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.research.costs import TransactionCostModel

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


def test_slippage_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="slippage"):
        TransactionCostModel(SCHEDULE, slippage_bps=Decimal(-1))


def test_the_round_trip_fraction_is_positive_for_a_real_trade() -> None:
    model = TransactionCostModel(SCHEDULE, slippage_bps=Decimal(5))

    fraction = model.round_trip_fraction(Exchange.NSE, 100, Money.of("500"))

    assert fraction > Decimal(0)


def test_more_slippage_never_lowers_the_round_trip_cost() -> None:
    quiet = TransactionCostModel(SCHEDULE, slippage_bps=Decimal(0))
    noisy = TransactionCostModel(SCHEDULE, slippage_bps=Decimal(20))

    quiet_cost = quiet.round_trip_fraction(Exchange.NSE, 100, Money.of("500"))
    noisy_cost = noisy.round_trip_fraction(Exchange.NSE, 100, Money.of("500"))

    assert noisy_cost > quiet_cost


def test_adjusted_return_subtracts_the_round_trip_cost_from_gross() -> None:
    model = TransactionCostModel(SCHEDULE, slippage_bps=Decimal(0))
    gross = Decimal("0.01")

    adjusted = model.adjusted_return(gross, Exchange.NSE, 100, Money.of("500"))

    assert adjusted == gross - model.round_trip_fraction(Exchange.NSE, 100, Money.of("500"))


def test_the_label_names_the_schedule_and_slippage() -> None:
    model = TransactionCostModel(SCHEDULE, slippage_bps=Decimal(5))

    assert model.label == "test+5bps"
