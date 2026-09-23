"""EM-183: `PortfolioCostModel`, `PortfolioEconomics` (position fragmentation) and
`TurnoverCalculator` at the canonical ₹50,000 production capital."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.portfolio import ClosedTrade, TradeDirection
from emporos.backtest.robustness.portfolio_economics import (
    PRODUCTION_CAPITAL,
    PortfolioCostModel,
    PortfolioEconomics,
    TurnoverCalculator,
)
from emporos.domain.fees import FeeSchedule
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money

D = Decimal
SCHEDULE = FeeSchedule(
    name="test",
    effective_from=date(2026, 1, 1),
    brokerage_flat=Money.of("20"),
    brokerage_percent=Decimal("0.03"),
    brokerage_minimum=Money.of("5"),
    stt_sell_percent=Decimal("0.025"),
    exchange_transaction_percent={Exchange.NSE: Decimal("0.0030699")},
    sebi_per_crore=Money.of("10"),
    stamp_duty_buy_percent=Decimal("0.003"),
    gst_percent=Decimal("18"),
)


def cost_model(spread: str = "2", slippage: str = "5") -> PortfolioCostModel:
    return PortfolioCostModel(SCHEDULE, Decimal(spread), Decimal(slippage))


def test_production_capital_is_fifty_thousand_rupees() -> None:
    assert Money.of("50000") == PRODUCTION_CAPITAL


def test_spread_and_slippage_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        PortfolioCostModel(SCHEDULE, Decimal(-1), Decimal(0))


class TestCostComponents:
    def test_components_sum_to_the_total(self) -> None:
        components = cost_model().components_for(Exchange.NSE, 100, Money.of("100"))

        assert components.total == (
            components.brokerage + components.statutory + components.spread + components.slippage
        )

    def test_total_bps_matches_the_total_as_a_fraction_of_notional(self) -> None:
        components = cost_model().components_for(Exchange.NSE, 100, Money.of("100"))

        expected = components.bps(components.total)
        assert components.total_bps == expected

    def test_spread_and_slippage_scale_with_their_own_bps(self) -> None:
        tight = cost_model(spread="1", slippage="1").components_for(
            Exchange.NSE, 100, Money.of("100")
        )
        wide = cost_model(spread="10", slippage="10").components_for(
            Exchange.NSE, 100, Money.of("100")
        )

        assert wide.spread.amount == tight.spread.amount * 10
        assert wide.slippage.amount == tight.slippage.amount * 10
        # brokerage and statutory charges never move with spread/slippage assumptions
        assert wide.brokerage == tight.brokerage
        assert wide.statutory == tight.statutory


class TestFragmentation:
    def test_capital_splits_evenly_across_concurrent_positions(self) -> None:
        economics = PortfolioEconomics(cost_model())

        scenario = economics.fragmentation_scenario(5, Exchange.NSE, Money.of("100"))

        assert scenario.capital_per_position == Money.of("10000")  # 50000 / 5
        assert scenario.quantity == 100  # 10000 / 100

    @pytest.mark.parametrize("concurrent_positions", [3, 4, 5, 10])
    def test_fragmentation_scenarios_compute_for_every_required_position_count(
        self, concurrent_positions: int
    ) -> None:
        economics = PortfolioEconomics(cost_model())

        price = Money.of("500")
        scenario = economics.fragmentation_scenario(concurrent_positions, Exchange.NSE, price)

        assert scenario.quantity > 0
        edge = scenario.minimum_gross_edge_bps
        assert edge is not None and edge > D(0)

    def test_higher_fragmentation_raises_the_minimum_edge_once_share_rounding_is_out_of_the_way(
        self,
    ) -> None:
        """Fixed costs (brokerage minimum, SEBI fee) bite harder on a smaller fragment — true in
        general, but floor-rounding the share quantity can obscure it at some price/capital
        combinations, so this compares two fragment counts (5 and 10) at a price (₹100) where
        ₹50,000/5 and ₹50,000/10 both divide evenly and no rounding loss is in play."""
        economics = PortfolioEconomics(cost_model())
        price = Money.of("100")

        at_5 = economics.fragmentation_scenario(5, Exchange.NSE, price)
        at_10 = economics.fragmentation_scenario(10, Exchange.NSE, price)

        assert at_5.minimum_gross_edge_bps is not None and at_10.minimum_gross_edge_bps is not None
        assert at_10.minimum_gross_edge_bps > at_5.minimum_gross_edge_bps

    def test_too_fine_a_fragment_to_buy_one_share_reports_no_cost_not_an_error(self) -> None:
        economics = PortfolioEconomics(cost_model())

        scenario = economics.fragmentation_scenario(1000, Exchange.NSE, Money.of("10000"))

        assert scenario.quantity == 0
        assert scenario.costs is None
        assert scenario.minimum_gross_edge_bps is None

    def test_needs_at_least_one_concurrent_position(self) -> None:
        with pytest.raises(ValueError, match="concurrent position"):
            PortfolioEconomics(cost_model()).fragmentation_scenario(0, Exchange.NSE, Money.of("1"))


def trade(
    quantity: int = 100,
    entry: str = "100",
    exit_: str = "101",
    hours: float = 1,
    opened: datetime = datetime(2026, 3, 2, 4, 0, tzinfo=UTC),
) -> ClosedTrade:
    return ClosedTrade(
        instrument_id="NSE:1", direction=TradeDirection.LONG, quantity=quantity,
        opened_at=opened, closed_at=opened + timedelta(hours=hours),
        entry_price=Money.of(entry), exit_price=Money.of(exit_),
        gross_pnl=Money.of(str((Decimal(exit_) - Decimal(entry)) * quantity)),
        fees=Money.of("10"), strategy_run_id="r1",
    )  # fmt: skip


class TestTurnover:
    def test_needs_at_least_one_trading_day(self) -> None:
        with pytest.raises(ValueError, match="trading day"):
            TurnoverCalculator().evaluate([], PRODUCTION_CAPITAL, 0)

    def test_no_trades_is_zero_turnover_and_utilization(self) -> None:
        report = TurnoverCalculator().evaluate([], PRODUCTION_CAPITAL, 5)

        assert report.trade_count == 0
        assert report.total_turnover == Money.zero()
        assert report.capital_utilization == Decimal(0)

    def test_turnover_is_entry_plus_exit_notional_across_all_trades(self) -> None:
        trades = [
            trade(quantity=100, entry="100", exit_="101"),
            trade(quantity=50, entry="200", exit_="199"),
        ]

        report = TurnoverCalculator().evaluate(trades, PRODUCTION_CAPITAL, 1)

        expected = Money.of("10000") + Money.of("10100") + Money.of("10000") + Money.of("9950")
        assert report.total_turnover == expected
        assert report.average_daily_turnover == expected  # one trading day

    def test_capital_utilization_is_time_weighted(self) -> None:
        # 10,000 notional held for exactly half of one 24h "trading day" against 50,000 capital:
        # deployed capital-days = 10000 * 0.5 = 5000; available = 50000 * 1 = 50000 -> 10%.
        held_half_a_day = trade(quantity=100, entry="100", exit_="100", hours=12)

        report = TurnoverCalculator().evaluate([held_half_a_day], PRODUCTION_CAPITAL, 1)

        assert report.capital_utilization == Decimal("0.1")
