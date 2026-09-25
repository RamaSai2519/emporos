"""EM-223: the swing simulator against hand-computed books. Costs are zero unless a test says."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.unit.research.swing.support import (
    ZERO_SCHEDULE,
    ArtifactSet,
    Scripted,
    dataset,
    free_costs,
    hold,
    series,
    sessions,
)

from emporos.domain.fees import DeliveryCharges
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.adjustments import ActionKind, AdjustmentFactor, AdjustmentLedger
from emporos.research.swing.costs import BENCHMARK, CostScenario, SwingCostModel
from emporos.research.swing.rules import Intent
from emporos.research.swing.simulator import FillPrice, SwingConfig, SwingSimulator

X, Y = "NSE:1", "NSE:2"
DAYS = sessions(5)


def config(capital: str = "10000", positions: int = 1, ceiling: str = "1") -> SwingConfig:
    return SwingConfig(Decimal(capital), positions, Decimal(ceiling))


def rise() -> list[tuple[str, str]]:
    return [("100", "100"), ("100", "110"), ("110", "121"), ("121", "121"), ("121", "121")]


class TestOnePosition:
    def test_it_buys_at_the_next_open_and_sells_at_the_next_open_after_the_signal_ends(
        self,
    ) -> None:
        data = dataset(series(X, DAYS, rise()))
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[2]), config(), free_costs()).run()

        (trade,) = run.trades
        assert (trade.entry_day, trade.exit_day) == (DAYS[1], DAYS[3])
        assert (trade.entry_price, trade.exit_price, trade.quantity) == (100, 121, 100)
        assert trade.net_pnl == 2100
        assert run.equity == (10000, 11000, 12100, 12100, 12100)
        assert run.daily_pnl == (0, 1000, 1100, 0, 0)
        assert run.invested_days == 2
        assert run.days_in_cash == 3

    def test_what_it_still_holds_at_the_end_is_sold_at_the_last_close_net(self) -> None:
        data = dataset(series(X, DAYS, rise()))
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        (trade,) = run.trades
        assert (trade.exit_day, trade.exit_price) == (DAYS[4], 121)
        assert run.equity[-1] == 12100

    def test_a_name_kept_by_the_next_decision_is_not_traded_again(self) -> None:
        data = dataset(series(X, DAYS, rise()))
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        assert len(run.trades) == 1

    def test_whole_shares_only_the_remainder_stays_cash(self) -> None:
        data = dataset(series(X, DAYS, [("300", "300")] * 5))
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config("1000"), free_costs()).run()

        assert run.trades[0].quantity == 3  # 3 x 300 = 900 of 1,000
        assert run.equity[1] == 1000

    def test_a_price_above_the_slot_is_skipped_and_counted(self) -> None:
        data = dataset(series(X, DAYS, [("20000", "20000")] * 5))
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        assert run.trades == ()
        assert run.skipped_entries == 4  # it is wanted, and cannot be afforded, at every decision
        assert run.equity == (10000,) * 5
        assert run.days_in_cash == 5

    def test_it_never_holds_more_than_the_cash_allows(self) -> None:
        data = dataset(series(X, DAYS, [("100", "100")] * 5))
        wants_both = Scripted(lambda c: [Intent(X), Intent(Y)] if c.index < 3 else [])
        two = dataset(series(X, DAYS, [("100", "100")] * 5), series(Y, DAYS, [("100", "100")] * 5))

        run = SwingSimulator(two, wants_both, config("10000", 2), free_costs()).run()

        assert sorted(t.quantity for t in run.trades) == [50, 50]  # equity / 2 each
        del data


class TestSplitsAndArtifacts:
    def test_a_split_changes_the_share_count_and_not_the_value(self) -> None:
        ledger = AdjustmentLedger(
            [AdjustmentFactor(X, DAYS[3], Decimal("0.5"), ActionKind.SPLIT, "s")]
        )
        raw = [("100", "100")] * 3 + [("50", "60"), ("60", "66")]
        data = dataset(series(X, DAYS, raw, ledger))

        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        assert run.equity == (10000, 10000, 10000, 12000, 13200)  # 200 shares after the ex-date
        (trade,) = run.trades
        assert trade.quantity == 100  # bought as 100 raw shares
        assert trade.net_pnl == 3200

    def test_an_artifact_gap_is_not_earned(self) -> None:
        raw = [("100", "100"), ("100", "100"), ("140", "141"), ("141", "142"), ("142", "143")]
        data = dataset(series(X, DAYS, raw, artifacts=ArtifactSet({X: [DAYS[2]]})))

        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        # bought at 100 on DAYS[1]; the 40% gap into DAYS[2] is flattened: only the day counts
        assert run.equity[1] == 10000
        assert run.equity[2] == Decimal(10000) * Decimal(141) / Decimal(140)


class TestCosts:
    def test_fees_and_slippage_come_out_of_the_cash_exactly(self) -> None:
        schedule_costs = SwingCostModel(_delivery(), BENCHMARK)
        data = dataset(series(X, DAYS, [("500", "500")] * 5))

        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[3]), config("50000"), schedule_costs).run()

        charges = DeliveryCharges(_delivery())
        buy_price, sell_price = Decimal("500") * Decimal("1.001"), Decimal("500") * Decimal("0.999")
        qty = 99  # 100 shares would cost 50,050 + fees: over the 50,000 slot
        buy_fees = charges.for_trade(
            Exchange.NSE, OrderSide.BUY, qty, Money(buy_price)
        ).total.amount
        sell_fees = charges.for_trade(
            Exchange.NSE, OrderSide.SELL, qty, Money(sell_price)
        ).total.amount
        (trade,) = run.trades
        assert trade.quantity == qty
        assert trade.fees == buy_fees + sell_fees
        assert trade.net_pnl == qty * (sell_price - buy_price) - buy_fees - sell_fees
        assert run.fees == trade.fees
        assert run.equity[-1] == 50000 + trade.net_pnl

    def test_the_adverse_scenario_costs_more_than_the_benchmark(self) -> None:
        data = dataset(series(X, DAYS, [("500", "500")] * 5))
        adverse = CostScenario("adverse", Decimal("1.5"), Decimal(25))

        def final(scenario: CostScenario) -> Decimal:
            costs = SwingCostModel(_delivery(), scenario)
            return (
                SwingSimulator(data, hold(X, DAYS[0], DAYS[3]), config("50000"), costs)
                .run()
                .equity[-1]
            )

        assert final(adverse) < final(CostScenario("b", Decimal(1), Decimal(10)))

    def test_a_scenario_cannot_loosen_the_schedule(self) -> None:
        with pytest.raises(ValueError, match="loosen"):
            CostScenario("cheap", Decimal("0.5"), Decimal(10))

    def test_a_calculator_needs_a_delivery_schedule(self) -> None:
        with pytest.raises(ValueError, match="delivery"):
            SwingCostModel(_intraday(), BENCHMARK)


class TestTiming:
    def test_the_strategy_sees_bars_up_to_the_decision_close_and_no_further(self) -> None:
        data = dataset(series(X, DAYS, [(str(i), str(i)) for i in range(1, 6)]))
        strategy = Scripted(lambda c: [])

        SwingSimulator(data, strategy, config(), free_costs()).run()

        for context in strategy.seen:
            last = context.view.history(X, 100)[-1]
            assert last.close.amount == context.index + 1
            assert context.view.sessions()[-1] == context.day
        assert [c.index for c in strategy.seen] == [0, 1, 2, 3]  # never decides on the last session

    def test_a_signal_is_filled_at_the_next_open_not_the_signal_close(self) -> None:
        raw = [("100", "100"), ("130", "131"), ("131", "131"), ("131", "131"), ("131", "131")]
        data = dataset(series(X, DAYS, raw))

        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        assert run.trades[0].entry_price == 130  # the next open, not the 100 close it decided on

    def test_holdings_report_how_long_they_have_been_held(self) -> None:
        data = dataset(series(X, DAYS, [("100", "100")] * 5))
        strategy = Scripted(lambda c: [Intent(X)])

        SwingSimulator(data, strategy, config(), free_costs()).run()

        held = [c.holdings[X].sessions_held for c in strategy.seen if X in c.holdings]
        assert held == [0, 1, 2]  # filled on session 1, decided on after sessions 1, 2, 3


class TestAvailability:
    def test_a_sell_waits_for_the_first_session_the_name_trades(self) -> None:
        x_days = [DAYS[0], DAYS[1], DAYS[2], DAYS[4]]  # no bar on DAYS[3]
        data = dataset(
            series(X, x_days, [("100", "100"), ("100", "100"), ("100", "100"), ("110", "110")]),
            series(Y, DAYS, [("5", "5")] * 5),
        )
        strategy = Scripted(lambda c: [Intent(X)] if c.index < 2 else [Intent(Y)])

        run = SwingSimulator(data, strategy, config(), free_costs()).run()

        x_trade = next(t for t in run.trades if t.instrument_id == X)
        assert x_trade.exit_day == DAYS[4]  # ordered out at the DAYS[2] close, no bar on DAYS[3]

    def test_a_name_with_no_bar_on_the_fill_day_is_not_bought_and_the_miss_is_counted(self) -> None:
        data = dataset(
            series(X, [DAYS[0], DAYS[2], DAYS[3], DAYS[4]], [("100", "100")] * 4),
            series(Y, DAYS, [("5", "5")] * 5),
        )
        strategy = Scripted(lambda c: [Intent(X)] if c.index == 0 else [])

        run = SwingSimulator(data, strategy, config(), free_costs()).run()

        assert run.trades == ()
        assert (
            run.skipped_entries == 1
        )  # ordered at the DAYS[0] close, no bar to fill it on DAYS[1]

    def test_membership_gates_what_a_strategy_may_be_offered(self) -> None:
        class OnlyY:
            def is_member(self, instrument_id: str, day: date) -> bool:
                return instrument_id == Y

        data = dataset(series(X, DAYS, [("1", "1")] * 5), series(Y, DAYS, [("1", "1")] * 5))
        strategy = Scripted(lambda c: [])

        SwingSimulator(data, strategy, config(), free_costs(), OnlyY()).run()

        assert all(c.tradable == {Y} for c in strategy.seen)


class TestSizing:
    def test_a_conviction_multiple_scales_the_slot_up_to_the_ceiling(self) -> None:
        data = dataset(series(X, DAYS, [("100", "100")] * 5), series(Y, DAYS, [("100", "100")] * 5))
        strategy = Scripted(
            lambda c: [Intent(X, Decimal("1.5")), Intent(Y, Decimal("5"))]
            if c.index == 0
            else [Intent(X), Intent(Y)]
        )

        run = SwingSimulator(data, strategy, config("10000", 2, "2"), free_costs()).run()

        quantities = {t.instrument_id: t.quantity for t in run.trades}
        assert quantities[X] == 75  # 1.5 x the 5,000 slot
        assert (
            quantities[Y] == 25
        )  # asked 5x, capped at 2x = 10,000, but only 2,500 of cash is left

    def test_a_multiple_is_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            Intent(X, Decimal(0))

    def test_a_config_is_correct_by_construction(self) -> None:
        for bad in ((0, 1, 1), (1000, 0, 1), (1000, 1, "0.5")):
            with pytest.raises(ValueError, match="positive|at least"):
                SwingConfig(Decimal(bad[0]), int(bad[1]), Decimal(bad[2]))

    def test_the_strategy_may_only_want_names_in_the_data(self) -> None:
        data = dataset(series(X, DAYS, [("1", "1")] * 5))

        with pytest.raises(ValueError, match="not in the data"):
            SwingSimulator(
                data, Scripted(lambda c: [Intent("NSE:404")]), config(), free_costs()
            ).run()


class TestRun:
    def test_pnl_by_instrument_sums_the_round_trips(self) -> None:
        data = dataset(series(X, DAYS, rise()))
        strategy = Scripted(lambda c: [Intent(X)] if c.index in (0, 1) else [])

        run = SwingSimulator(data, strategy, config(), free_costs()).run()

        assert run.pnl_by_instrument == {X: Decimal(1000) + 0} or run.pnl_by_instrument[X] == sum(
            t.net_pnl for t in run.trades
        )

    def test_a_dataset_with_no_sessions_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no sessions"):
            SwingSimulator(dataset(), Scripted(lambda c: []), config(), free_costs()).run()


def _delivery():  # type: ignore[no-untyped-def]
    return ZERO_SCHEDULE.__class__(**{**ZERO_SCHEDULE.__dict__, **_REAL_DELIVERY_FIELDS})


_REAL_DELIVERY_FIELDS = {
    "name": "d",
    "brokerage_flat": Money.of("20"),
    "brokerage_percent": Decimal("0.1"),
    "brokerage_minimum": Money.of("5"),
    "stt_sell_percent": Decimal("0.1"),
    "stt_buy_percent": Decimal("0.1"),
    "exchange_transaction_percent": {Exchange.NSE: Decimal("0.0030699")},
    "sebi_per_crore": Money.of("10"),
    "stamp_duty_buy_percent": Decimal("0.015"),
    "gst_percent": Decimal("18"),
    "dp_charge_per_sale": Money.of("20"),
}


def _intraday():  # type: ignore[no-untyped-def]
    from dataclasses import replace

    from emporos.domain.fees import TradeProduct

    return replace(ZERO_SCHEDULE, product=TradeProduct.INTRADAY)


class TestHandComputedParity:
    """A book worked out on paper: buy 100 at 500, sell 100 at 510, real delivery charges."""

    def test_the_round_trip_matches_the_hand_computed_charges(self) -> None:
        # buy 100 x 500 = 50,000: brokerage 20.00 + STT 50.00 + exchange 1.53 + SEBI 0.05
        #   + stamp 7.50 + GST 3.88 (18% of 21.58) = 82.96
        # sell 100 x 510 = 51,000: brokerage 20.00 + STT 51.00 + exchange 1.57 + SEBI 0.05
        #   + DP 20.00 + GST 7.49 (18% of 41.62) = 100.11
        raw = [("500", "500"), ("500", "505"), ("510", "510"), ("510", "510"), ("510", "510")]
        data = dataset(series(X, DAYS, raw))
        costs = SwingCostModel(_delivery(), CostScenario("no-slippage", Decimal(1), Decimal(0)))

        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[2]), config("50100"), costs).run()

        (trade,) = run.trades
        assert trade.quantity == 100  # 101 would need 50,500 + fees, more than the 50,100 slot
        assert trade.fees == Decimal("183.07")
        assert trade.net_pnl == Decimal("816.93")  # 100 x 10 - 82.96 - 100.11
        assert run.equity[-1] == Decimal("50916.93")
        assert run.equity[1] == Decimal("50100") - Decimal("82.96") + 100 * Decimal(
            5
        )  # marked at 505


class TestCashYield:
    def test_idle_cash_accrues_the_annual_yield_per_session(self) -> None:
        data = dataset(series(X, sessions(253), [("100", "100")] * 253))
        config = SwingConfig(Decimal(10000), 1, cash_yield=Decimal("0.05"))

        run = SwingSimulator(data, Scripted(lambda c: []), config, free_costs()).run()

        assert run.equity[0] == 10000  # nothing carried into the first session
        assert run.equity[252] == pytest.approx(
            Decimal(10500), rel=Decimal("0.0000001")
        )  # 252 accruals

    def test_no_yield_no_accrual(self) -> None:
        data = dataset(series(X, DAYS, [("100", "100")] * 5))

        run = SwingSimulator(data, Scripted(lambda c: []), config(), free_costs()).run()

        assert run.equity == (10000,) * 5

    def test_a_negative_yield_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not negative"):
            SwingConfig(Decimal(1000), 1, cash_yield=Decimal("-0.01"))


class TestSlice:
    def test_a_run_sliced_from_a_day_restarts_from_the_previous_close(self) -> None:
        data = dataset(series(X, DAYS, rise()))
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        part = run.slice_from(DAYS[2])

        assert part.days == run.days[2:]
        assert part.capital == run.equity[1]  # 11,000
        assert part.equity == run.equity[2:]
        assert part.daily_pnl[0] == run.equity[2] - run.equity[1]
        assert part.trades == ()  # the only trade was bought on DAYS[1], before the slice
        assert part.invested_days == sum(run.invested_flags[2:])

    def test_a_slice_from_the_first_day_is_the_run(self) -> None:
        data = dataset(series(X, DAYS, rise()))
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        assert run.slice_from(DAYS[0]) is run

    def test_a_slice_past_the_end_is_refused(self) -> None:
        data = dataset(series(X, DAYS, rise()))
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[4]), config(), free_costs()).run()

        with pytest.raises(ValueError, match="no session on or after"):
            run.slice_from(date(2030, 1, 1))


class TestStartDay:
    def test_the_run_starts_on_the_start_day_and_earlier_bars_are_history_a_signal_can_read(
        self,
    ) -> None:
        data = dataset(series(X, DAYS, rise()))
        seen: list[int] = []

        def script(c):  # type: ignore[no-untyped-def]
            seen.append(len(c.view.history(X, 100)))
            return []

        cfg = SwingConfig(Decimal(10000), 1, start_day=DAYS[2])
        run = SwingSimulator(data, Scripted(script), cfg, free_costs()).run()

        assert run.days == tuple(DAYS[2:])
        assert seen == [3, 4]  # the first decision (session 2) sees the 3 bars up to it

    def test_a_start_day_after_the_data_is_refused(self) -> None:
        data = dataset(series(X, DAYS, rise()))

        with pytest.raises(ValueError, match="no session on or after"):
            late = SwingConfig(Decimal(1), 1, start_day=date(2030, 1, 1))
            SwingSimulator(data, Scripted(lambda c: []), late, free_costs()).run()


class TestFillAtTheClose:
    """PROFIT_PLAN §10: a core cell decides at a close and fills at the NEXT close."""

    def test_the_buy_and_the_sell_trade_the_next_sessions_close_not_its_open(self) -> None:
        data = dataset(
            series(
                X, DAYS, [("100", "100"), ("90", "110"), ("95", "121"), ("50", "60"), ("60", "60")]
            )
        )
        cfg = SwingConfig(Decimal(10000), 1, fill=FillPrice.CLOSE)
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[2]), cfg, free_costs()).run()

        (trade,) = run.trades
        assert (trade.entry_day, trade.exit_day) == (DAYS[1], DAYS[3])
        assert (trade.entry_price, trade.exit_price) == (110, 60)  # the closes, not 90 and 50
        assert trade.quantity == 90  # 9,900 of 10,000 at 110
        assert run.equity[1] == 10000  # marked at the same close it was bought at

    def test_costs_are_the_same_schedule_and_slippage_on_the_close_price(self) -> None:
        data = dataset(series(X, DAYS, [("100", "100")] * 5))
        costs = SwingCostModel(ZERO_SCHEDULE, CostScenario("slip", Decimal(1), Decimal(100)))
        cfg = SwingConfig(Decimal(10000), 1, fill=FillPrice.CLOSE)
        run = SwingSimulator(data, hold(X, DAYS[0], DAYS[2]), cfg, costs).run()

        (trade,) = run.trades
        assert trade.entry_price == Decimal("101")  # 100 bps against the buyer
        assert trade.exit_price == Decimal("99")

    def test_the_default_is_still_the_open(self) -> None:
        assert SwingConfig(Decimal(1), 1).fill is FillPrice.OPEN
