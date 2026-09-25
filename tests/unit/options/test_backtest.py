"""EM-226: the end-of-day spread backtester, on chains small enough to work out by hand.

Unless a test says otherwise: one put credit spread, short 90 / long 80, expiring 30 days after
DAY0, a lot of 10 units, zero fees and zero slippage, so every rupee below is arithmetic."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from emporos.options.backtest import (
    BacktestResult,
    BacktestSettings,
    MissingExpirySnapshot,
    SpreadBacktester,
)
from emporos.options.exits import ExitReason, standard_policy
from emporos.options.fo_costs import FoFeeSchedule
from emporos.options.margin import SpanLikeMargin
from emporos.options.slippage import ADVERSE, BENCHMARK, SlippageScenario
from emporos.options.spread import SpreadPlan, Vertical
from tests.support.option_chains import (
    CALL,
    DAY0,
    PUT,
    ZERO_FEES,
    ScriptedPlanner,
    days_after,
    snapshot,
    source,
)

D = Decimal
EXPIRY = days_after(30)
PCS = SpreadPlan(EXPIRY, (Vertical(PUT, D(90), D(80)),))
CONDOR = SpreadPlan(EXPIRY, (Vertical(PUT, D(90), D(80)), Vertical(CALL, D(110), D(120))))
ZERO = SlippageScenario("zero", 0, D(0), D(1))


def put_chain(day: date, short: str, long: str, spot: str = "100", **kw: object) -> object:
    return snapshot(day, spot, EXPIRY, {(90, PUT): short, (80, PUT): long}, **kw)  # type: ignore[arg-type]


def run(
    *snapshots: object,
    plan: SpreadPlan = PCS,
    entry_days: frozenset[date] = frozenset({DAY0}),
    capital: str = "1000",
    lots: int = 1,
    slippage: SlippageScenario = ZERO,
    fees: FoFeeSchedule = ZERO_FEES,
    last: date | None = None,
) -> BacktestResult:
    backtester = SpreadBacktester(
        source(*snapshots),  # type: ignore[arg-type]
        ScriptedPlanner(plan, entry_days),
        standard_policy(),
        SpanLikeMargin(D("0.10")),
        fees,
        BacktestSettings(D(capital), lots, slippage),
    )
    return backtester.run(DAY0, last or days_after(60))


class TestExits:
    def test_half_the_credit_kept_closes_at_the_target(self) -> None:
        result = run(put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(1), "1.50", "0.50"))

        (trade,) = result.trades
        assert (trade.entry_day, trade.exit_day) == (DAY0, days_after(1))
        assert trade.reason is ExitReason.PROFIT_TARGET
        assert (trade.credit_per_unit, trade.close_debit_per_unit) == (D(2), D(1))
        assert (trade.gross_pnl, trade.net_pnl, trade.units) == (D(10), D(10), 10)
        assert (trade.margin, trade.max_loss) == (D(88), D(80))  # (10 - 2) x 10, plus 10%

    def test_a_loss_of_twice_the_credit_closes_at_the_stop(self) -> None:
        result = run(put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(1), "8.00", "2.00"))

        (trade,) = result.trades
        assert trade.reason is ExitReason.STOP_LOSS
        assert (trade.close_debit_per_unit, trade.gross_pnl) == (D(6), D(-40))

    def test_seven_days_out_closes_on_time_at_the_marks(self) -> None:
        result = run(put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(23), "3.00", "1.00"))

        (trade,) = result.trades
        assert (trade.reason, trade.exit_day, trade.gross_pnl) == (
            ExitReason.TIME,
            days_after(23),
            D(0),
        )

    def test_nothing_fires_so_the_spread_stays_open_and_is_marked_to_market(self) -> None:
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(days_after(1), "2.50", "1.00"),
            last=days_after(1),
        )

        assert result.trades == ()
        assert [d.pnl for d in result.days] == [D(0), D(5)]  # (2 - 1.5) x 10 unrealised on day 1
        assert result.days[-1].equity == D(1005)


class TestExpiry:
    @pytest.mark.parametrize(
        ("spot", "gross"), [("120", D(20)), ("95", D(20)), ("85", D(-30)), ("70", D(-80))]
    )
    def test_the_spread_settles_on_the_index_close_between_full_profit_and_max_loss(
        self, spot: str, gross: Decimal
    ) -> None:
        result = run(put_chain(DAY0, "3.00", "1.00"), put_chain(EXPIRY, "0.10", "0.05", spot=spot))

        (trade,) = result.trades
        assert (trade.reason, trade.exit_day, trade.gross_pnl) == (ExitReason.EXPIRY, EXPIRY, gross)

    def test_the_worst_case_equals_the_recorded_max_loss(self) -> None:
        result = run(put_chain(DAY0, "3.00", "1.00"), put_chain(EXPIRY, "0", "0", spot="50"))

        (trade,) = result.trades
        assert trade.gross_pnl == -trade.max_loss

    def test_a_missing_expiry_chain_stops_the_run_instead_of_guessing(self) -> None:
        with pytest.raises(MissingExpirySnapshot):
            run(put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(31), "0", "0", spot="100"))

    def test_an_iron_condor_loses_only_on_the_wing_the_market_went_through(self) -> None:
        both = {(90, PUT): "3.00", (80, PUT): "1.00", (110, CALL): "2.50", (120, CALL): "1.00"}
        result = run(
            snapshot(DAY0, "100", EXPIRY, both),
            snapshot(EXPIRY, "125", EXPIRY, both),
            plan=CONDOR,
        )

        (trade,) = result.trades
        assert trade.credit_per_unit == D("3.5")  # (3 - 1) + (2.5 - 1)
        assert (trade.close_debit_per_unit, trade.gross_pnl) == (D(10), D(-65))
        assert trade.max_loss == D(65) and trade.margin == D("71.5")


class TestLiquidity:
    def test_a_leg_that_did_not_trade_blocks_the_entry(self) -> None:
        result = run(put_chain(DAY0, "3.00", "1.00", untraded=frozenset({(80, PUT)})))

        assert result.trades == () and result.skipped["not_tradable"] == 1

    def test_a_leg_missing_from_the_chain_blocks_the_entry(self) -> None:
        result = run(snapshot(DAY0, "100", EXPIRY, {(90, PUT): "3.00"}))

        assert result.trades == () and result.skipped["not_tradable"] == 1

    def test_the_liquidity_floor_is_in_contracts(self) -> None:
        thin = put_chain(DAY0, "3.00", "1.00", traded=3)
        backtester = SpreadBacktester(
            source(thin),  # type: ignore[arg-type]
            ScriptedPlanner(PCS, frozenset({DAY0})),
            standard_policy(),
            SpanLikeMargin(),
            ZERO_FEES,
            BacktestSettings(D(1000), 1, ZERO, min_contracts=5),
        )

        assert backtester.run(DAY0, DAY0).skipped["not_tradable"] == 1

    def test_an_exit_waits_for_the_day_every_leg_trades_again(self) -> None:
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(days_after(1), "1.50", "0.50", untraded=frozenset({(80, PUT)})),
            put_chain(days_after(2), "1.50", "0.50"),
        )

        (trade,) = result.trades
        assert trade.exit_day == days_after(2)
        assert result.skipped["exit_deferred_untraded_leg"] == 1

    def test_a_leg_gone_from_the_chain_leaves_the_spread_open_and_counted(self) -> None:
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            snapshot(days_after(1), "100", EXPIRY, {(90, PUT): "3.00"}),
            last=days_after(1),
        )

        assert result.trades == () and result.skipped["unmarked"] == 1


class TestEntryGuards:
    def test_a_credit_that_does_not_cover_the_slippage_is_not_opened(self) -> None:
        result = run(put_chain(DAY0, "1.00", "1.00"))

        assert result.trades == () and result.skipped["no_credit"] == 1

    def test_a_credit_beyond_the_width_is_data_error_not_a_free_lunch(self) -> None:
        result = run(put_chain(DAY0, "12.00", "1.00"))

        assert result.trades == () and result.skipped["credit_beyond_width"] == 1

    def test_too_little_capital_for_the_margin_skips_the_entry(self) -> None:
        result = run(put_chain(DAY0, "3.00", "1.00"), capital="87")

        assert result.trades == () and result.skipped["margin"] == 1

    def test_the_margin_is_the_line_exactly(self) -> None:
        assert len(run(put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(1), "1.50", "0.50"),
                       capital="88").trades) == 1  # fmt: skip

    def test_a_planner_that_declines_leaves_the_book_flat_and_counted(self) -> None:
        result = run(put_chain(DAY0, "3.00", "1.00"), entry_days=frozenset())

        assert result.trades == () and result.skipped["no_plan"] == 1
        assert [d.equity for d in result.days] == [D(1000)]

    def test_a_day_without_a_chain_is_counted_and_skipped(self) -> None:
        class Holey:
            def days(self) -> list[date]:
                return [DAY0]

            def snapshot(self, day: date) -> None:
                return None

        backtester = SpreadBacktester(
            Holey(), ScriptedPlanner(PCS, frozenset({DAY0})), standard_policy(), SpanLikeMargin(),
            ZERO_FEES, BacktestSettings(D(1000), 1, ZERO),
        )  # fmt: skip

        assert backtester.run(DAY0, DAY0).skipped["no_chain"] == 1


class TestSequencing:
    def test_a_spread_closed_today_is_not_replaced_today(self) -> None:
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(days_after(1), "1.50", "0.50"),
            put_chain(days_after(2), "3.00", "1.00"),
            entry_days=frozenset({DAY0, days_after(1), days_after(2)}),
            last=days_after(2),
        )

        assert len(result.trades) == 1
        # day 1 closed the first trade and opened nothing; day 2 opened the second (marked flat)
        assert result.days[1].equity == D(1010)
        assert result.days[2].equity == D(1010)

    def test_only_one_spread_is_open_at_a_time(self) -> None:
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(days_after(1), "3.00", "1.00"),
            entry_days=frozenset({DAY0, days_after(1)}),
            last=days_after(1),
        )

        assert result.trades == () and result.days[-1].equity == D(1000)


class TestSizeAndCosts:
    def test_two_lots_double_the_pnl_and_the_margin(self) -> None:
        one = run(put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(1), "1.50", "0.50"))
        two = run(put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(1), "1.50", "0.50"), lots=2)

        assert (two.trades[0].gross_pnl, two.trades[0].units) == (D(20), 20)
        assert two.trades[0].margin == 2 * one.trades[0].margin

    def test_the_lot_size_in_force_that_day_sets_the_units(self) -> None:
        result = run(
            put_chain(DAY0, "3.00", "1.00", lot_size=75), put_chain(days_after(1), "1.50", "0.50")
        )

        assert result.trades[0].units == 75 and result.trades[0].gross_pnl == D("75")

    def test_benchmark_slippage_pays_up_on_every_leg_both_ways(self) -> None:
        # entry: sell 3.00 -> 2.90, buy 1.00 -> 1.10 (credit 1.80); the target needs 0.90 kept:
        # marks 2.00 / 0.70 give a mark debit of 1.30, so 0.50 kept: nothing yet.
        # exit at marks 1.20 / 0.50: mark debit 0.70, kept 1.10 >= 0.90 -> fill buy 1.20 -> 1.30,
        # sell 0.50 -> 0.40 (0.05 + 0.0025, floored to the tick): debit 0.90.
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(days_after(1), "2.00", "0.70"),
            put_chain(days_after(2), "1.20", "0.50"),
            slippage=BENCHMARK,
        )

        (trade,) = result.trades
        assert trade.credit_per_unit == D("1.80")
        assert trade.close_debit_per_unit == D("0.90")
        assert trade.gross_pnl == D("9.0")

    def test_the_adverse_scenario_costs_more_than_the_benchmark(self) -> None:
        chains = (put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(1), "1.00", "0.20"))
        bench = run(*chains, slippage=BENCHMARK)
        adverse = run(*chains, slippage=ADVERSE)

        assert adverse.trades[0].net_pnl < bench.trades[0].net_pnl

    def test_charges_are_taken_on_every_leg_order_in_and_out(self) -> None:
        brokerage_only = FoFeeSchedule(
            "b", DAY0, False, D(20), D(0), D(0), D(0), D(0), D(0), D(0), D(0)
        )
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(days_after(1), "1.50", "0.50"),
            fees=brokerage_only,
        )

        (trade,) = result.trades
        assert trade.charges == D(80)  # two legs in, two legs out, Rs 20 each
        assert trade.net_pnl == D(10) - D(80)

    def test_an_expiry_settlement_pays_no_exit_brokerage_only_stt_on_a_long_leg_in_the_money(
        self,
    ) -> None:
        stt_only = FoFeeSchedule(
            "s", DAY0, False, D(0), D(0), D("0.15"), D(0), D(0), D(0), D(0), D(0)
        )
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(EXPIRY, "0", "0", spot="75"),  # both legs in the money; the long by 5
            fees=stt_only,
        )

        (trade,) = result.trades
        assert trade.charges == D("0.075")  # 0.15% of (5 x 10 units)


class TestSeries:
    def test_daily_pnl_adds_up_to_the_trades_net_once_flat(self) -> None:
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(days_after(1), "2.50", "1.00"),
            put_chain(days_after(2), "1.50", "0.50"),
        )

        assert sum(result.daily_pnl(), D(0)) == result.net_pnl == D(10)
        assert result.days[-1].equity == D(1010)

    def test_the_run_is_deterministic(self) -> None:
        chains = (put_chain(DAY0, "3.00", "1.00"), put_chain(days_after(1), "1.50", "0.50"))

        assert run(*chains) == run(*chains)

    def test_months_and_drawdown_come_from_the_daily_series(self) -> None:
        result = run(
            put_chain(DAY0, "3.00", "1.00"),
            put_chain(days_after(1), "5.00", "1.00"),  # marked down: debit 4, loss 2 x 10
            put_chain(days_after(2), "1.50", "0.50"),
        )

        assert result.monthly_pnl() == {(2026, 1): D(10)}
        assert result.max_drawdown() == D(20) / D(1000)

    def test_a_result_with_no_days_has_no_drawdown(self) -> None:
        assert BacktestResult((), (), {}).max_drawdown() == D(0)

    def test_settings_refuse_nonsense(self) -> None:
        for bad in (
            lambda: BacktestSettings(D(0), 1, ZERO),
            lambda: BacktestSettings(D(1), 0, ZERO),
            lambda: BacktestSettings(D(1), 1, ZERO, min_contracts=0),
        ):
            with pytest.raises(ValueError):
                bad()
