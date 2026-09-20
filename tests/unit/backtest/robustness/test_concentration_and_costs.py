"""Concentration shares and cost re-pricing, worked by hand."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.portfolio import ClosedTrade, TradeDirection
from emporos.backtest.robustness.benchmark import BenchmarkLoader, CostScenario
from emporos.backtest.robustness.concentration import ConcentrationCheck
from emporos.backtest.robustness.cost_sensitivity import CostSensitivity
from emporos.domain.money import Money

D = Decimal
T0 = datetime(2026, 3, 2, 4, 0, tzinfo=UTC)


def trade(
    net: str, symbol: str = "NSE:1", day: int = 0, fees: str = "10", qty: int = 10,
    entry: str = "100", exit_: str = "101",
) -> ClosedTrade:  # fmt: skip
    gross = D(net) + D(fees)
    opened = T0 + timedelta(days=day)
    return ClosedTrade(
        symbol, TradeDirection.LONG, qty, opened, opened + timedelta(minutes=30),
        Money.of(entry), Money.of(exit_), Money(gross), Money.of(fees),
    )  # fmt: skip


class TestConcentration:
    def test_shares_of_the_total_by_instrument_month_and_top_trades(self) -> None:
        trades = [
            trade("300", "NSE:1", 0),   # March
            trade("100", "NSE:2", 1),   # March
            trade("100", "NSE:2", 40),  # April
            trade("-100", "NSE:3", 41),  # April
        ]  # fmt: skip

        report = ConcentrationCheck(top_trades=1).measure(trades)

        assert report.net_pnl == D(400)
        assert (report.top_instrument, report.top_instrument_share) == ("NSE:1", D("0.75"))
        assert (report.top_month, report.top_month_share) == ("2026-03", D(1))  # 400 of 400
        assert report.top_trades_share == D("0.75")

    def test_a_share_can_exceed_one_when_the_rest_lost(self) -> None:
        report = ConcentrationCheck(2).measure([trade("500", "NSE:1"), trade("-300", "NSE:2", 1)])

        assert report.top_instrument_share == D(500) / D(200)

    def test_a_run_with_no_profit_has_no_shares(self) -> None:
        report = ConcentrationCheck(3).measure([trade("-50"), trade("20", day=1)])

        assert not report.defined
        assert report.top_month_share is None and report.top_trades_share is None

    def test_months_follow_the_ist_close_not_utc(self) -> None:
        late = ClosedTrade(
            "NSE:1", TradeDirection.LONG, 1, datetime(2026, 3, 31, 18, 0, tzinfo=UTC),
            datetime(2026, 3, 31, 19, 0, tzinfo=UTC), Money.of("1"), Money.of("2"),
            Money.of("50"), Money.of("0"),
        )  # fmt: skip

        assert ConcentrationCheck(1).measure([late]).top_month == "2026-04"  # already April in IST

    def test_needs_at_least_one_top_trade(self) -> None:
        with pytest.raises(ValueError):
            ConcentrationCheck(0)


def scenario(name: str, fees: str, bps: str) -> CostScenario:
    return CostScenario(name=name, fee_multiplier=D(fees), extra_slippage_bps=D(bps))


class TestCostSensitivity:
    def test_repricing_is_gross_less_scaled_fees_less_slippage_on_both_sides(self) -> None:
        # gross 60, fees 10; entry 10 x 100 = 1,000 and exit 10 x 101 = 1,010 (2,010 traded)
        trades = [trade("50", fees="10")]
        outcomes = CostSensitivity().evaluate(
            trades,
            [
                scenario("as_run", "1", "0"),
                scenario("dear", "2", "10"),
                scenario("cheap", "1", "-5"),
            ],
        )

        by_name = {o.name: o.net_pnl for o in outcomes}
        assert by_name["as_run"] == D(50)
        assert by_name["dear"] == D(60) - D(20) - D("2.01")  # 2,010 x 10 bps
        assert by_name["cheap"] == D(50) + D("1.005")  # 2,010 x 5 bps given back

    def test_profitable_flag_and_order_follow_the_scenarios(self) -> None:
        outcomes = CostSensitivity().evaluate(
            [trade("5", fees="10")], [scenario("a", "1", "0"), scenario("b", "3", "0")]
        )

        assert [o.name for o in outcomes] == ["a", "b"]
        assert [o.profitable for o in outcomes] == [True, False]  # 15 - 10 > 0; 15 - 30 < 0

    def test_the_shipped_scenarios_include_the_benchmark_unchanged(self) -> None:
        trades = [trade("50"), trade("-20", day=1)]
        benchmark = BenchmarkLoader().load()

        outcomes = CostSensitivity().evaluate(trades, benchmark.cost_scenarios)

        as_run = next(o for o in outcomes if o.name == "benchmark")
        assert as_run.net_pnl == D(30)
