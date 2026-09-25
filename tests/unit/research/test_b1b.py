"""EM-234, cell B1b: the no-stop exit policy, the cell designs, the per-spread breakdown, the
closure test and the wider wing arms."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.support.option_chains import SyntheticMarket

from emporos.domain.orders import OrderSide
from emporos.options.backtest import BacktestResult, SpreadTrade
from emporos.options.chain import OptionRight
from emporos.options.exits import ExitReason, OpenView, standard_policy
from emporos.options.fo_costs import FoFeeScheduleLibrary
from emporos.options.slippage import BENCHMARK, FRICTIONLESS
from emporos.options.spread import SpreadPlan, Vertical
from emporos.research.option_screen.b1 import (
    B1_DESIGN,
    B1B_DESIGN,
    B1Arms,
    B1Backtests,
    B1Inputs,
    B1Parameters,
)
from emporos.research.option_screen.breakdown import CostBreakdown
from emporos.research.option_screen.ledger import OptionIdentity, OptionRecord
from emporos.research.option_screen.report import breakdown_lines, closure_lines
from emporos.research.option_screen.run import ArmResult, B1Screen
from emporos.research.option_screen.screen import ArmOutcome, judge
from emporos.research.swing.bootstrap import BlockBootstrap, RuinReport

D = Decimal
B1B_GRID = {"filter": ("on", "off"), "wing_cap": ("5000", "10000")}


class TestNoStopExitPolicy:
    def view(self, pnl: str, days: int = 20) -> OpenView:
        return OpenView(D(10), D(10) - D(pnl), days)

    def test_the_standard_policy_still_stops_at_twice_the_credit(self) -> None:
        assert standard_policy().decide(self.view("-20")) is ExitReason.STOP_LOSS

    def test_without_a_stop_a_huge_loss_does_not_close_the_spread(self) -> None:
        policy = standard_policy(stop=None)

        assert policy.decide(self.view("-90")) is None  # the wing is the stop

    def test_without_a_stop_the_target_and_the_time_exit_remain(self) -> None:
        policy = standard_policy(stop=None)

        assert policy.decide(self.view("5")) is ExitReason.PROFIT_TARGET
        assert policy.decide(self.view("-3", days=7)) is ExitReason.TIME


class TestTheCellDesigns:
    def test_b1b_declares_four_arms_on_filter_and_wing_cap(self) -> None:
        arms = B1Arms.declared(B1B_GRID)

        assert [a.as_point() for a in arms] == [
            {"filter": "on", "wing_cap": "5000"},
            {"filter": "on", "wing_cap": "10000"},
            {"filter": "off", "wing_cap": "5000"},
            {"filter": "off", "wing_cap": "10000"},
        ]
        assert next(a.label for a in arms) == "filter=on wing_cap=5000"

    def test_b1b_fixes_k_depth_and_drops_the_stop(self) -> None:
        for arm in B1Arms.declared(B1B_GRID):
            assert (arm.k, arm.ladder_depth, arm.design.stop) == (D("1.0"), 2, None)
            assert arm.design is B1B_DESIGN

    def test_only_the_wide_wing_needs_the_ruin_check(self) -> None:
        arms = {a.label: a.aggressive for a in B1Arms.declared(B1B_GRID)}

        assert arms == {
            "filter=on wing_cap=5000": False,
            "filter=on wing_cap=10000": True,
            "filter=off wing_cap=5000": False,
            "filter=off wing_cap=10000": True,
        }

    def test_each_b1b_arm_has_two_neighbours(self) -> None:
        adjacent = B1Arms.adjacent(B1Arms.declared(B1B_GRID))

        assert adjacent["filter=on wing_cap=5000"] == [
            "filter=on wing_cap=10000",
            "filter=off wing_cap=5000",
        ]
        assert {len(v) for v in adjacent.values()} == {2}

    def test_b1_arms_keep_their_declared_points_and_the_stop(self) -> None:
        (first, *_) = B1Arms.declared({"k": ("1.0",), "filter": ("on",), "ladder_depth": ("2",)})

        assert first.as_point() == {"k": "1.0", "filter": "on", "ladder_depth": "2"}
        assert first.label == "k=1.0 filter=on depth=2"
        assert first.design is B1_DESIGN and first.design.stop == D(2)

    def test_a_grid_that_names_no_cell_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly a cell's axes"):
            B1Arms.declared({"filter": ("on",)})

    def test_a_non_positive_wing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="wing cap"):
            B1Parameters(D("1.0"), True, 2, D(0))


PLAN = SpreadPlan(date(2024, 1, 25), (Vertical(OptionRight.PUT, D(90), D(80)),))


def trade(gross: str, charges: str, credit: str = "10", reason: ExitReason = ExitReason.EXPIRY,
          max_loss: str = "5000") -> SpreadTrade:  # fmt: skip
    return SpreadTrade(
        date(2024, 1, 1), date(2024, 1, 20), PLAN, 1, 75, D(credit), D(0), reason,
        D(gross), D(charges), D(6000), D(max_loss),
    )  # fmt: skip


class TestCostBreakdown:
    def test_per_spread_figures(self) -> None:
        run = BacktestResult(
            (
                trade("300", "100", reason=ExitReason.PROFIT_TARGET),
                trade("100", "100"),
                trade("-1500", "100", reason=ExitReason.STOP_LOSS, max_loss="4000"),
            ),
            (),
            {},
        )

        b = CostBreakdown.of(run)

        assert b.trips == 3 and b.credit == 750  # 10 x 75 units
        assert b.gross == pytest.approx((300 + 100 - 1500) / 3) and b.charges == 100
        assert b.net == pytest.approx((200 + 0 - 1600) / 3)
        assert b.win_rate == pytest.approx(1 / 3)  # the second nets exactly 0: not a win
        assert (b.average_win, b.average_loss) == (200, pytest.approx(-800))
        assert b.worst_spread == -1600 and b.max_loss == 5000
        assert b.exits == {"profit_target": 1, "expiry": 1, "stop_loss": 1}
        assert b.charges_share_of_credit == pytest.approx(100 / 750)
        assert b.gross_to_charges == pytest.approx(-1100 / 3 / 100)

    def test_a_run_without_trades_is_all_zero(self) -> None:
        b = CostBreakdown.of(BacktestResult((), (), {}))

        assert (b.trips, b.gross, b.average_win, b.average_loss, b.worst_spread) == (
            0, 0.0, None, None, 0.0,
        )  # fmt: skip
        assert b.charges_share_of_credit is None and b.gross_to_charges is None

    def test_the_worst_spread_is_zero_when_nothing_lost(self) -> None:
        assert CostBreakdown.of(BacktestResult((trade("500", "100"),), (), {})).worst_spread == 0


RUIN = RuinReport(10_000, 20, 252, 1, 0.01, 0.10, 0.2)


def arm_result(gross: str, charges: str, arm: B1Parameters | None = None) -> ArmResult:
    from tests.unit.research.test_option_screen import outcome

    arm = arm or B1Arms.declared(B1B_GRID)[0]
    base = outcome()
    frictionless = BacktestResult((trade(gross, charges),), (), {})
    full = ArmOutcome(
        base.run, base.stats, base.adverse_stats, RUIN, base.first_day, base.capital,
        frictionless, BacktestResult((trade("-10", charges),), (), {}),
    )  # fmt: skip
    return ArmResult(arm, full, 0.5, judge(full, 0.5, aggressive=False))


class TestClosureTestAndReport:
    def test_the_cell_is_closed_when_no_arm_reaches_twice_its_charges(self) -> None:
        arms = B1Arms.declared(B1B_GRID)
        lines = closure_lines([arm_result("150", "100", a) for a in arms])

        assert lines[-1].startswith("CLOSED:")
        assert "index put-spread selling is closed at this capital" in lines[-1]
        assert "1.50x" in lines[1]

    def test_one_arm_at_twice_its_charges_keeps_the_lane_open(self) -> None:
        arms = B1Arms.declared(B1B_GRID)
        results = [arm_result("150", "100", a) for a in arms[:3]] + [
            arm_result("200", "100", arms[3])
        ]

        assert closure_lines(results)[-1].startswith("CLOSURE TEST NOT TRIGGERED")

    def test_a_cell_without_the_test_prints_nothing(self) -> None:
        b1 = B1Parameters(D("1.0"), True, 2)

        assert closure_lines([arm_result("150", "100", b1)]) == []
        assert closure_lines([]) == []

    def test_the_breakdown_prints_the_three_scenarios(self) -> None:
        text = "\n".join(breakdown_lines(arm_result("300", "100")))

        assert "zero slippage" in text and "benchmark" in text and "adverse" in text
        assert "of credit" in text and "worst spread" in text and "max loss" in text

    def test_the_ledger_line_carries_the_breakdown(self) -> None:
        result = arm_result("300", "100")
        identity = OptionIdentity(
            "b1b", result.arm.as_point(), "NIFTY", date(2017, 10, 1), date(2024, 12, 31), D(100_000)
        )

        document = OptionRecord(
            identity, result, datetime(2026, 9, 25, tzinfo=UTC), "p"
        ).as_document()

        json.dumps(document)  # serialisable
        breakdown = document["breakdown"]
        assert isinstance(breakdown, dict)
        assert set(breakdown) == {"zero_slippage", "benchmark", "adverse"}
        assert breakdown["zero_slippage"]["gross"] == 300.0


MARKET = SyntheticMarket()


def inputs() -> B1Inputs:
    return B1Inputs(
        MARKET.source(), MARKET.nifty, MARKET.vix, frozenset(), MARKET.futures_calendar(),
        FoFeeScheduleLibrary.from_directory().earliest, D(100_000),
    )  # fmt: skip


class TestTheRun:
    def test_no_b1b_spread_is_stopped_and_the_wing_cap_holds(self) -> None:
        screen = B1Screen(inputs(), MARKET.days[-1], BlockBootstrap(paths=20))
        results = screen.run(B1Arms.declared(B1B_GRID))

        assert len(results) == 4
        widest = {}
        for r in results:
            trades = r.outcome.run.trades
            assert all(t.reason is not ExitReason.STOP_LOSS for t in trades)
            cap = r.arm.wing_cap
            for t in trades:
                assert t.plan.verticals[0].width * (t.units // t.lots) <= cap
            widest[r.arm.label] = max((t.plan.verticals[0].width for t in trades), default=D(0))
            assert r.outcome.frictionless_run is not None and r.outcome.adverse_run is not None
        assert widest["filter=off wing_cap=10000"] >= widest["filter=off wing_cap=5000"]

    def test_zero_slippage_never_does_worse_than_benchmark_on_the_same_book(self) -> None:
        backtests = B1Backtests(inputs())
        arm = B1Arms.declared(B1B_GRID)[2]
        first, last = MARKET.days[252], MARKET.days[-1]

        free = backtests.build(arm, FRICTIONLESS).run(first, last)
        paid = backtests.build(arm, BENCHMARK).run(first, last)

        assert sum(t.gross_pnl for t in free.trades) >= sum(t.gross_pnl for t in paid.trades)

    def test_the_frictionless_fill_is_the_mark(self) -> None:
        assert FRICTIONLESS.fill_price(OrderSide.SELL, D("100.35"), D("0.05")) == D("100.35")
        assert FRICTIONLESS.fill_price(OrderSide.BUY, D("100.35"), D("0.05")) == D("100.35")

    def test_b1b_is_a_declared_recipe_with_arms_of_the_declaration(self) -> None:
        declared = replace(B1B_DESIGN)  # the design is a plain value
        assert declared.slug == "b1b-nifty-put-spread-no-stop" and declared.closure_multiple == 2
        assert Path("config/experiments/b1b-nifty-put-spread-no-stop.yaml").exists()
