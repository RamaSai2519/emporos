"""The replay's costs come from the program's own schedules and scenarios, not from copies."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.domain.fees import TradeProduct
from emporos.eventtrader.replay.program_costs import (
    IntradayCosts,
    OptionCosts,
    RoutedCosts,
    SwingCosts,
    program_costs,
)
from emporos.eventtrader.replay.records import Scenario, TradeLeg
from emporos.eventtrader.risk.models import Product
from emporos.eventtrader.stages.models import Instrument, Side
from emporos.options.fo_costs import FoFeeScheduleLibrary
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.screen_costs import ScreenCostModel
from tests.unit.eventtrader.replay.fakes import MON, TUE, at

D = Decimal
SCHEDULES = EarliestBeforeFirst(FeeScheduleLibrary.from_directory())
BENCHMARK = BenchmarkLoader().load()
FO = FoFeeScheduleLibrary.from_directory().earliest
DELIVERY = FeeScheduleLibrary.from_directory(product=TradeProduct.DELIVERY).earliest


def leg(
    instrument: Instrument, product: Product, side: Side, quantity: int, entry: str, exit_: str
) -> TradeLeg:
    return TradeLeg(
        "NSE:2885", instrument, product, side, quantity, at(MON, 11, 50), D(entry), at(TUE, 15, 0),
        D(exit_),
    )  # fmt: skip


INTRADAY = leg(Instrument.CASH_INTRADAY, Product.INTRADAY, Side.LONG, 500, "100", "103")
SWING = leg(Instrument.CASH_SWING, Product.SWING, Side.LONG, 250, "100", "104")
OPTION = leg(Instrument.CALL, Product.OPTION, Side.LONG, 250, "20", "30")


def test_intraday_is_the_screeners_round_trip_fraction_of_the_position() -> None:
    costs = IntradayCosts(ScreenCostModel(SCHEDULES), BENCHMARK)

    benchmark, adverse = (costs.cost(INTRADAY, s) for s in (Scenario.BENCHMARK, Scenario.ADVERSE))

    # 5 bps a side of Rs 50,000 is Rs 50 on its own, before any statutory charge
    assert D(50) < benchmark < adverse
    # the adverse scenario is fees x1.5 and 10 bps more a side: at least Rs 100 more than benchmark
    assert adverse - benchmark > D(50)


def test_a_short_costs_the_same_intraday_as_a_long() -> None:
    costs = IntradayCosts(ScreenCostModel(SCHEDULES), BENCHMARK)
    short = leg(Instrument.CASH_INTRADAY, Product.INTRADAY, Side.SHORT, 500, "100", "97")

    assert costs.cost(short, Scenario.BENCHMARK) == pytest.approx(
        costs.cost(INTRADAY, Scenario.BENCHMARK), rel=D("0.05")
    )


def test_delivery_pays_slippage_on_both_sides_and_fees() -> None:
    costs = SwingCosts(DELIVERY)

    benchmark, adverse = (costs.cost(SWING, s) for s in (Scenario.BENCHMARK, Scenario.ADVERSE))

    # 10 bps on Rs 25,000 in and about Rs 26,000 out is over Rs 50 of slippage alone
    assert benchmark > D(50) and adverse > D(120)


def test_a_delivery_short_is_a_bug_not_a_free_trade() -> None:
    short = leg(Instrument.CASH_SWING, Product.SWING, Side.SHORT, 250, "100", "97")

    with pytest.raises(ValueError, match="long only"):
        SwingCosts(DELIVERY).cost(short, Scenario.BENCHMARK)


def test_an_option_pays_a_tick_and_half_a_percent_a_fill_and_two_orders_of_charges() -> None:
    costs = OptionCosts(FO)

    benchmark, adverse = (costs.cost(OPTION, s) for s in (Scenario.BENCHMARK, Scenario.ADVERSE))

    # buy at 20 + 0.05 + 0.10 = 20.15 (Rs 0.15 x 250), sell at 30 - 0.05 - 0.15 = 29.80
    # (Rs 0.20 x 250): Rs 87.50 of slippage, then Rs 40 of brokerage and the rest
    assert benchmark > D("127.5")
    assert adverse > benchmark


def test_the_routed_costs_price_every_instrument_the_pipeline_can_choose() -> None:
    routed = program_costs(SCHEDULES, BENCHMARK, DELIVERY, FO)
    put = leg(Instrument.PUT, Product.OPTION, Side.LONG, 250, "20", "10")

    for trade in (INTRADAY, SWING, OPTION, put):
        assert routed.cost(trade, Scenario.ADVERSE) > 0


def test_an_instrument_without_a_model_is_an_error() -> None:
    with pytest.raises(ValueError, match="no cost model"):
        RoutedCosts({}).cost(INTRADAY, Scenario.BENCHMARK)
