"""The coin-flip control: same trades, coin sides, same engine; the p-value arithmetic."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from random import Random

from emporos.eventtrader.pipeline import PipelineDecision, TradePlan, Verdict
from emporos.eventtrader.replay.control import (
    CoinFlipControl,
    CoinFlipDecider,
    ControlResult,
    plan_with_view,
)
from emporos.eventtrader.replay.engine import ReplayEngine
from emporos.eventtrader.replay.records import Scenario
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.stages.models import Instrument, Side
from tests.unit.eventtrader.fakes import NOON, item
from tests.unit.eventtrader.replay.test_engine import COSTS, EXEC, NoContext, ev, market, plan

D = Decimal


def swing(instrument: Instrument = Instrument.CASH_SWING, side: Side = Side.LONG) -> TradePlan:
    return TradePlan(instrument, side, 3.0, 6.0, 5, 70)


class TestPlanWithView:
    def test_intraday_takes_the_side(self) -> None:
        p = plan(Instrument.CASH_INTRADAY, Side.LONG)

        assert plan_with_view(p, Side.SHORT).side is Side.SHORT
        assert plan_with_view(p, Side.LONG) == p

    def test_a_cash_swing_that_flips_to_short_becomes_the_put(self) -> None:
        out = plan_with_view(swing(), Side.SHORT)

        assert (out.instrument, out.side) == (Instrument.PUT, Side.LONG)  # a put is bought

    def test_calls_and_puts_swap_and_keep_the_stop_target_and_horizon(self) -> None:
        put = plan_with_view(swing(Instrument.CALL), Side.SHORT)
        call = plan_with_view(swing(Instrument.PUT), Side.LONG)

        assert put.instrument is Instrument.PUT and call.instrument is Instrument.CALL
        assert (put.stop_pct, put.target_pct, put.hold_days) == (3.0, 6.0, 5)

    def test_a_put_that_flips_to_long_on_the_cash_swing_horizon_is_a_call(self) -> None:
        assert plan_with_view(swing(Instrument.PUT), Side.LONG).instrument is Instrument.CALL


class TestDecider:
    async def test_only_trades_are_touched_and_the_coin_is_seeded(self) -> None:
        trade = PipelineDecision("E1", Verdict.TRADE, plan(Instrument.CASH_INTRADAY, Side.LONG))
        no = PipelineDecision("E2", Verdict.JUDGE_NO)
        decisions = {"E1": trade, "E2": no}

        sides = [
            (await CoinFlipDecider(decisions, Random(1)).decide(item(event_id="E1"))).plan
            for _ in range(2)
        ]
        untouched = await CoinFlipDecider(decisions, Random(1)).decide(item(event_id="E2"))

        assert sides[0] == sides[1] and sides[0] is not None
        assert untouched is no

    async def test_over_many_draws_both_sides_appear(self) -> None:
        trade = PipelineDecision("E1", Verdict.TRADE, plan(Instrument.CASH_INTRADAY, Side.LONG))
        coin = CoinFlipDecider({"E1": trade}, Random(7))

        got = {(await coin.decide(item(event_id="E1"))).plan.side for _ in range(40)}  # type: ignore[union-attr]

        assert got == {Side.LONG, Side.SHORT}


class TestResult:
    def result(self, real: int, coins: list[int]) -> ControlResult:
        return ControlResult(len(coins), D(real), tuple(D(c) for c in coins), Scenario.ADVERSE)

    def test_p_is_one_plus_the_coin_runs_at_least_as_good_over_one_plus_runs(self) -> None:
        r = self.result(100, [50, 100, 150, -20])

        assert r.at_least_as_good == 2 and r.p_value == 3 / 5

    def test_a_real_run_better_than_every_coin_run_has_the_smallest_possible_p(self) -> None:
        r = self.result(100, [0] * 999 + [-5])

        assert r.p_value == 1 / 1001

    def test_the_mean_of_the_coins(self) -> None:
        assert self.result(0, [10, 20]).coin_mean == D(15)


class TestControl:
    async def test_the_control_replays_only_the_approved_trades_through_the_same_engine(
        self,
    ) -> None:
        m = market()
        events = [ev("E1"), ev("E2", at_=NOON + timedelta(hours=1)), ev("E3", name="NSE:1594")]
        decisions = {
            "E1": PipelineDecision("E1", Verdict.TRADE, plan()),
            "E2": PipelineDecision("E2", Verdict.JUDGE_NO),
            "E3": PipelineDecision("E3", Verdict.TRADE, plan()),
        }

        def engine_for(decider: object) -> ReplayEngine:
            return ReplayEngine(decider, NoContext(), m, RiskEngine(), COSTS, EXEC)  # type: ignore[arg-type]

        control = CoinFlipControl(engine_for, decisions, runs=20, seed=5)
        first = await control.run(events, D(0), Scenario.ADVERSE)
        again = await CoinFlipControl(engine_for, decisions, runs=20, seed=5).run(
            events, D(0), Scenario.ADVERSE
        )

        assert first.coin_nets == again.coin_nets  # seeded
        # flat prices: every trade is squared off flat, so each run loses exactly its costs
        assert all(n == D(-200) for n in first.coin_nets)  # 2 trades x Rs 50,000 x 0.2%
        assert first.p_value == 1 / 21  # a real run at 0 beats every coin run at -200
