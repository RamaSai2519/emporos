"""EM-226: the option chain, spread, slippage, cost, margin, exit and entry rules, each pinned with
numbers worked out by hand."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.domain.orders import OrderSide
from emporos.options.chain import ChainSnapshot, ExpiryChain, OptionQuote
from emporos.options.chain_source import InMemoryChainSource
from emporos.options.entry import (
    AlwaysOpen,
    IronCondorTemplate,
    PutCreditSpreadTemplate,
    SigmaStrikes,
    SpreadEntry,
    TargetDte,
)
from emporos.options.exits import (
    ExitPolicy,
    ExitReason,
    MinDaysToExpiry,
    OpenView,
    ProfitTarget,
    StopLoss,
    standard_policy,
)
from emporos.options.fo_costs import (
    FoCostModel,
    FoFeeError,
    FoFeeSchedule,
    FoFeeScheduleLibrary,
    FoFeeScheduleParser,
)
from emporos.options.margin import SpanLikeMargin, max_loss
from emporos.options.slippage import ADVERSE, BENCHMARK, SlippageScenario
from emporos.options.spread import Leg, LegFill, SpreadPlan, Vertical
from tests.support.option_chains import CALL, DAY0, PUT, TICK, days_after, snapshot

D = Decimal


class TestQuote:
    def test_a_contract_that_traded_marks_at_its_close(self) -> None:
        quote = OptionQuote(D(100), PUT, D("5.00"), D("4.80"), 10, 3)

        assert (quote.tradable, quote.mark) == (True, D("5.00"))

    def test_a_contract_that_did_not_trade_marks_at_its_settlement_and_cannot_be_filled(
        self,
    ) -> None:
        quote = OptionQuote(D(100), PUT, D("5.00"), D("4.80"), 10, 0)

        assert (quote.tradable, quote.mark) == (False, D("4.80"))

    def test_a_zero_close_is_not_tradable_even_with_volume(self) -> None:
        assert not OptionQuote(D(100), PUT, D(0), D(0), 0, 5).tradable

    @pytest.mark.parametrize(
        "bad", [dict(strike=D(0)), dict(close=D(-1)), dict(contracts_traded=-1)]
    )
    def test_impossible_quotes_are_refused(self, bad: dict[str, Decimal | int]) -> None:
        fields: dict[str, object] = dict(
            strike=D(100), right=PUT, close=D(1), settle=D(1), open_interest=0, contracts_traded=0
        )
        with pytest.raises(ValueError):
            OptionQuote(**{**fields, **bad})  # type: ignore[arg-type]


class TestSnapshotAndSource:
    def test_days_to_expiry_and_the_sorted_expiries(self) -> None:
        snap = snapshot(DAY0, "100", days_after(30), {(90, PUT): "1"})

        assert snap.days_to(days_after(30)) == 30 and snap.expiry_dates == (days_after(30),)

    def test_a_snapshot_needs_a_positive_spot_and_a_lot(self) -> None:
        snap = snapshot(DAY0, "100", days_after(30), {})
        with pytest.raises(ValueError):
            replace(snap, underlying_close=D(0))
        with pytest.raises(ValueError):
            replace(snap, lot_size=0)

    def test_the_in_memory_source_is_ordered_and_refuses_duplicate_days(self) -> None:
        a = snapshot(days_after(2), "100", days_after(30), {})
        b = snapshot(days_after(1), "100", days_after(30), {})

        source = InMemoryChainSource([a, b])

        assert list(source.days()) == [days_after(1), days_after(2)]
        assert source.snapshot(days_after(1)) is b and source.snapshot(days_after(9)) is None
        with pytest.raises(ValueError, match="two snapshots"):
            InMemoryChainSource([a, a])

    def test_an_expiry_chain_lists_its_strikes(self) -> None:
        snap = snapshot(
            DAY0, "100", days_after(30), {(90, PUT): "1", (80, PUT): "1", (110, CALL): "1"}
        )
        chain: ExpiryChain = snap.expiries[days_after(30)]

        assert chain.strikes == (D(80), D(90), D(110))
        assert chain.quote(D(90), PUT) is not None and chain.quote(D(90), CALL) is None


class TestSpread:
    def test_a_put_vertical_needs_its_long_leg_below_the_short(self) -> None:
        Vertical(PUT, D(90), D(80))
        with pytest.raises(ValueError, match="farther out"):
            Vertical(PUT, D(90), D(95))
        with pytest.raises(ValueError, match="farther out"):
            Vertical(PUT, D(90), D(90))

    def test_a_call_vertical_needs_its_long_leg_above_the_short(self) -> None:
        Vertical(CALL, D(110), D(120))
        with pytest.raises(ValueError, match="farther out"):
            Vertical(CALL, D(110), D(105))

    def test_every_short_leg_comes_with_its_long_leg(self) -> None:
        legs = list(SpreadPlan(days_after(30), (Vertical(PUT, D(90), D(80)),)).legs)

        assert legs == [Leg(D(90), PUT, OrderSide.SELL), Leg(D(80), PUT, OrderSide.BUY)]

    @pytest.mark.parametrize(
        ("spot", "debit"),
        [(D(95), D(0)), (D(90), D(0)), (D(85), D(5)), (D(80), D(10)), (D(60), D(10))],
    )
    def test_a_put_vertical_settles_between_zero_and_its_width(
        self, spot: Decimal, debit: Decimal
    ) -> None:
        assert Vertical(PUT, D(90), D(80)).settlement_debit(spot) == debit

    @pytest.mark.parametrize(
        ("spot", "debit"), [(D(105), D(0)), (D(115), D(5)), (D(120), D(10)), (D(200), D(10))]
    )
    def test_a_call_vertical_settles_between_zero_and_its_width(
        self, spot: Decimal, debit: Decimal
    ) -> None:
        assert Vertical(CALL, D(110), D(120)).settlement_debit(spot) == debit

    def test_an_iron_condor_settles_only_its_losing_wing(self) -> None:
        condor = SpreadPlan(
            days_after(30), (Vertical(PUT, D(90), D(80)), Vertical(CALL, D(110), D(120)))
        )

        assert condor.settlement_debit(D(100)) == D(0)
        assert condor.settlement_debit(D(125)) == D(10)
        assert condor.settlement_debit(D(85)) == D(5)

    def test_the_worst_case_is_the_wider_wing(self) -> None:
        condor = SpreadPlan(
            days_after(30), (Vertical(PUT, D(90), D(70)), Vertical(CALL, D(110), D(120)))
        )

        assert condor.max_width == D(20)

    def test_two_verticals_of_one_right_or_an_overlapping_condor_or_none_are_refused(self) -> None:
        put = Vertical(PUT, D(90), D(80))
        with pytest.raises(ValueError, match="same right"):
            SpreadPlan(days_after(30), (put, Vertical(PUT, D(70), D(60))))
        with pytest.raises(ValueError, match="lie below"):
            SpreadPlan(days_after(30), (put, Vertical(CALL, D(85), D(95))))
        with pytest.raises(ValueError, match="one vertical"):
            SpreadPlan(days_after(30), ())

    def test_a_fill_knows_its_turnover(self) -> None:
        assert LegFill(Leg(D(90), PUT, OrderSide.SELL), D("2.5"), 75).turnover == D("187.5")


class TestSlippage:
    def test_the_fixed_scenarios_are_the_plans(self) -> None:
        assert (BENCHMARK.ticks, BENCHMARK.premium_fraction, BENCHMARK.fee_multiplier) == (
            1, D("0.005"), D(1)
        )  # fmt: skip
        assert (ADVERSE.ticks, ADVERSE.premium_fraction, ADVERSE.fee_multiplier) == (
            2, D("0.015"), D("1.5")
        )  # fmt: skip

    def test_a_sell_receives_less_and_a_buy_pays_more_on_the_tick_grid(self) -> None:
        # 3.00: slip = 0.05 + 0.015 = 0.065 -> sell 2.935 floors to 2.90, buy 3.065 ceils to 3.10
        assert BENCHMARK.fill_price(OrderSide.SELL, D("3.00"), TICK) == D("2.90")
        assert BENCHMARK.fill_price(OrderSide.BUY, D("3.00"), TICK) == D("3.10")

    def test_the_adverse_scenario_is_harsher(self) -> None:
        # 3.00: slip = 0.10 + 0.045 = 0.145 -> sell 2.855 floors to 2.85, buy 3.145 ceils to 3.15
        assert ADVERSE.fill_price(OrderSide.SELL, D("3.00"), TICK) == D("2.85")
        assert ADVERSE.fill_price(OrderSide.BUY, D("3.00"), TICK) == D("3.15")

    def test_a_cheap_option_never_sells_below_zero(self) -> None:
        assert BENCHMARK.fill_price(OrderSide.SELL, D("0.05"), TICK) == D(0)

    def test_a_scenario_cannot_be_kinder_than_the_statutory_fees(self) -> None:
        with pytest.raises(ValueError, match="statutory"):
            SlippageScenario("x", 0, D(0), D("0.9"))
        with pytest.raises(ValueError, match="negative"):
            SlippageScenario("x", -1, D(0), D(1))


SCHEDULE = FoFeeSchedule(
    "test", date(2026, 1, 1), False,
    brokerage_flat_per_order=D(20), stt_sell_percent=D("0.15"), stt_exercise_percent=D("0.15"),
    exchange_transaction_percent=D("0.0355299"), sebi_per_crore=D(10),
    stamp_duty_buy_percent=D("0.003"), ipft_percent=D("0.002"), gst_percent=D(18),
)  # fmt: skip


class TestCosts:
    def test_a_sell_order_pays_stt_and_no_stamp(self) -> None:
        charges = FoCostModel(SCHEDULE).order(LegFill(Leg(D(90), PUT, OrderSide.SELL), D(100), 75))

        # turnover 7,500: exchange 2.6647425, sebi 0.0075, ipft 0.15, stt 11.25,
        # gst 18% of (20 + 2.6647425 + 0.0075 + 0.15) = 4.10800365
        assert charges.brokerage == D(20) and charges.stt == D("11.25") and charges.stamp == D(0)
        assert charges.exchange == D("2.6647425") and charges.sebi == D("0.0075")
        assert charges.ipft == D("0.15") and charges.gst == D("4.10800365")
        assert charges.total == D("38.18024615")

    def test_a_buy_order_pays_stamp_and_no_stt(self) -> None:
        charges = FoCostModel(SCHEDULE).order(LegFill(Leg(D(80), PUT, OrderSide.BUY), D(100), 75))

        assert charges.stt == D(0) and charges.stamp == D("0.225")

    def test_the_adverse_multiplier_scales_every_charge(self) -> None:
        fill = LegFill(Leg(D(90), PUT, OrderSide.SELL), D(100), 75)

        assert FoCostModel(SCHEDULE, D("1.5")).order(fill).total == D("38.18024615") * D("1.5")

    def test_every_leg_fill_is_its_own_order(self) -> None:
        fills = [
            LegFill(Leg(D(90), PUT, OrderSide.SELL), D(100), 75),
            LegFill(Leg(D(80), PUT, OrderSide.BUY), D(100), 75),
        ]

        assert FoCostModel(SCHEDULE).orders(fills).brokerage == D(40)

    def test_settlement_stt_is_on_the_intrinsic_value_of_long_legs(self) -> None:
        assert FoCostModel(SCHEDULE).expiry_stt(D(50)).total == D("0.075")

    def test_a_model_cannot_charge_less_than_the_schedule(self) -> None:
        with pytest.raises(ValueError, match="statutory"):
            FoCostModel(SCHEDULE, D("0.5"))

    def test_the_committed_schedule_is_unverified_and_holds_the_read_rates(self) -> None:
        library = FoFeeScheduleLibrary.from_directory()
        schedule = library.for_date(date(2026, 9, 25))

        assert schedule.verified is False
        assert (schedule.brokerage_flat_per_order, schedule.stt_sell_percent) == (D(20), D("0.15"))
        assert schedule.exchange_transaction_percent == D("0.0355299")
        assert library.earliest is schedule
        with pytest.raises(FoFeeError, match="in force"):
            library.for_date(date(2025, 1, 1))

    def test_a_float_rate_or_a_negative_one_or_a_missing_one_is_refused(
        self, tmp_path: Path
    ) -> None:
        good = Path("config/fees/fo/angelone-fo-options-2026-09-25.yaml").read_text()
        for name, text in {
            "float": good.replace('stt_sell_percent: "0.15"', "stt_sell_percent: 0.15"),
            "negative": good.replace('gst_percent: "18"', 'gst_percent: "-1"'),
            "missing": good.replace('ipft_percent: "0.002"\n', ""),
            "junk": good.replace('sebi_per_crore: "10"', 'sebi_per_crore: "ten"'),
        }.items():
            path = tmp_path / f"{name}.yaml"
            path.write_text(text)
            with pytest.raises(FoFeeError):
                FoFeeScheduleParser().parse(path)

    def test_an_empty_library_is_refused(self) -> None:
        with pytest.raises(FoFeeError, match="no F&O"):
            FoFeeScheduleLibrary([])


class TestMargin:
    def test_the_worst_case_loss_is_the_wider_wing_less_the_credit(self) -> None:
        plan = SpreadPlan(days_after(30), (Vertical(PUT, D(90), D(80)),))

        assert max_loss(plan, D(2), 10) == D(80)

    def test_margin_is_that_loss_plus_the_buffer(self) -> None:
        plan = SpreadPlan(days_after(30), (Vertical(PUT, D(90), D(80)),))

        assert SpanLikeMargin(D("0.10")).required(plan, D(2), 10) == D("88")
        assert SpanLikeMargin(D(0)).required(plan, D(2), 10) == D(80)

    def test_a_negative_buffer_is_refused(self) -> None:
        with pytest.raises(ValueError):
            SpanLikeMargin(D("-0.1"))


def view(credit: str = "2", debit: str = "2", dte: int = 30) -> OpenView:
    return OpenView(D(credit), D(debit), dte)


class TestExits:
    def test_the_stop_fires_when_the_loss_reaches_the_multiple_of_the_credit(self) -> None:
        assert StopLoss(D(2)).fires(view(debit="6"))  # lost 4 = 2 x 2, inclusive
        assert not StopLoss(D(2)).fires(view(debit="5.99"))

    def test_the_target_fires_when_the_fraction_of_the_credit_is_kept(self) -> None:
        assert ProfitTarget(D("0.5")).fires(view(debit="1"))  # kept 1 = half of 2, inclusive
        assert not ProfitTarget(D("0.5")).fires(view(debit="1.01"))

    def test_the_time_exit_fires_at_and_inside_the_days_left(self) -> None:
        assert MinDaysToExpiry(7).fires(view(dte=7)) and MinDaysToExpiry(7).fires(view(dte=3))
        assert not MinDaysToExpiry(7).fires(view(dte=8))

    def test_the_stop_is_tried_before_the_target_before_the_time_exit(self) -> None:
        policy = standard_policy()

        assert policy.decide(view(debit="6", dte=5)) is ExitReason.STOP_LOSS
        assert policy.decide(view(debit="1", dte=5)) is ExitReason.PROFIT_TARGET
        assert policy.decide(view(debit="2", dte=5)) is ExitReason.TIME
        assert policy.decide(view(debit="2", dte=30)) is None

    def test_the_order_of_a_policy_is_the_order_given(self) -> None:
        policy = ExitPolicy([MinDaysToExpiry(7), StopLoss(D(2))])

        assert policy.decide(view(debit="6", dte=5)) is ExitReason.TIME

    def test_a_view_reports_its_pnl(self) -> None:
        assert view(debit="0.5").pnl_per_unit == D("1.5")

    @pytest.mark.parametrize(
        "bad", [lambda: StopLoss(D(0)), lambda: ProfitTarget(D(0)), lambda: ProfitTarget(D("1.1")),
                lambda: MinDaysToExpiry(0)],
    )  # fmt: skip
    def test_nonsense_thresholds_are_refused(self, bad: object) -> None:
        with pytest.raises(ValueError):
            bad()  # type: ignore[operator]


class FixedVolatility:
    def __init__(self, vol: str | None) -> None:
        self._vol = None if vol is None else D(vol)

    def annual_volatility(self, day: date) -> Decimal | None:
        return self._vol


class TestEntry:
    def test_the_expiry_in_the_window_closest_to_the_target_is_chosen(self) -> None:
        snap = ChainSnapshot(
            DAY0, "T", D(100), 10, D(10), TICK,
            {days_after(n): ExpiryChain(days_after(n), {}) for n in (7, 21, 30, 44, 60)},
        )  # fmt: skip

        assert TargetDte(20, 45, 30).choose(snap) == days_after(30)
        assert TargetDte(20, 45, 40).choose(snap) == days_after(44)
        assert TargetDte(20, 45, 26).choose(snap) == days_after(30)  # 4 away beats 5 away
        assert TargetDte(50, 55, 52).choose(snap) is None

    def test_a_tie_goes_to_the_earlier_expiry(self) -> None:
        snap = ChainSnapshot(
            DAY0, "T", D(100), 10, D(10), TICK,
            {days_after(n): ExpiryChain(days_after(n), {}) for n in (26, 34)},
        )  # fmt: skip

        assert TargetDte(20, 45, 30).choose(snap) == days_after(26)

    def test_the_window_must_hold_the_target(self) -> None:
        with pytest.raises(ValueError):
            TargetDte(20, 45, 10)

    def test_short_strikes_round_away_from_the_money_to_the_step(self) -> None:
        # spot 10050, vol 10%, 365 days: one sigma = 10050 x 0.10 x 1 = 1005
        snap = snapshot(DAY0, "10050", DAY0.replace(year=2027), {})
        strikes = SigmaStrikes(FixedVolatility("0.10"), D(1), D(200))

        put = strikes.vertical(snap, DAY0.replace(year=2027), PUT)
        call = strikes.vertical(snap, DAY0.replace(year=2027), CALL)

        assert put == Vertical(PUT, D(9040), D(8840))  # raw 9045 floors to 9040
        assert call == Vertical(CALL, D(11060), D(11260))  # raw 11055 ceils to 11060

    def test_two_sigmas_reach_twice_as_far(self) -> None:
        snap = snapshot(DAY0, "10000", DAY0.replace(year=2027), {})
        put = SigmaStrikes(FixedVolatility("0.10"), D(2), D(100)).vertical(
            snap, DAY0.replace(year=2027), PUT
        )

        assert put == Vertical(PUT, D(8000), D(7900))

    def test_no_volatility_means_no_strikes(self) -> None:
        snap = snapshot(DAY0, "10000", days_after(30), {})

        assert (
            SigmaStrikes(FixedVolatility(None), D(1), D(100)).vertical(snap, days_after(30), PUT)
            is None
        )

    def test_the_templates_build_a_credit_spread_and_a_condor(self) -> None:
        expiry = DAY0.replace(year=2027)
        snap = snapshot(DAY0, "10000", expiry, {})
        strikes = SigmaStrikes(FixedVolatility("0.10"), D(1), D(100))

        put_plan = PutCreditSpreadTemplate(strikes).build(snap, expiry)
        condor = IronCondorTemplate(strikes).build(snap, expiry)

        assert put_plan is not None and [v.right for v in put_plan.verticals] == [PUT]
        assert condor is not None and {v.right for v in condor.verticals} == {PUT, CALL}
        blind = SigmaStrikes(FixedVolatility(None), D(1), D(100))
        assert PutCreditSpreadTemplate(blind).build(snap, expiry) is None
        assert IronCondorTemplate(blind).build(snap, expiry) is None

    def test_a_refusing_filter_or_no_expiry_means_no_plan(self) -> None:
        expiry = DAY0.replace(year=2027)
        snap = snapshot(DAY0, "10000", expiry, {})
        template = PutCreditSpreadTemplate(SigmaStrikes(FixedVolatility("0.10"), D(1), D(100)))

        class Closed:
            def allows(self, snapshot: ChainSnapshot, expiry: date) -> bool:
                return False

        assert (
            SpreadEntry([AlwaysOpen()], TargetDte(360, 370, 365), template).plan(snap) is not None
        )
        assert (
            SpreadEntry([AlwaysOpen(), Closed()], TargetDte(360, 370, 365), template).plan(snap)
            is None
        )
        assert SpreadEntry([AlwaysOpen()], TargetDte(20, 45, 30), template).plan(snap) is None


class TestMonthlyExpiries:
    def snap(self, *offsets: int) -> ChainSnapshot:
        return ChainSnapshot(
            DAY0, "T", D(100), 10, D(10), TICK,
            {days_after(n): ExpiryChain(days_after(n), {}) for n in offsets},
        )  # fmt: skip

    def test_only_expiries_that_are_also_future_expiries_count(self) -> None:
        from emporos.options.entry import MonthlyExpiries

        chooser = MonthlyExpiries({DAY0: frozenset({days_after(34), days_after(62)})}, 21, 49)

        # 27 is a weekly (no future); 34 is monthly and in the window; 62 is monthly but too far
        assert chooser.choose(self.snap(6, 13, 27, 34, 62)) == days_after(34)

    def test_the_nearer_of_two_qualifying_monthlies_is_chosen(self) -> None:
        from emporos.options.entry import MonthlyExpiries

        chooser = MonthlyExpiries({DAY0: frozenset({days_after(22), days_after(48)})}, 21, 49)

        assert chooser.choose(self.snap(22, 48)) == days_after(22)

    def test_the_window_edges_are_inclusive(self) -> None:
        from emporos.options.entry import MonthlyExpiries

        chooser = MonthlyExpiries({DAY0: frozenset({days_after(21), days_after(49)})}, 21, 49)

        assert chooser.choose(self.snap(21)) == days_after(21)
        assert chooser.choose(self.snap(49)) == days_after(49)
        assert chooser.choose(self.snap(20, 50)) is None

    def test_a_day_with_no_futures_listed_offers_no_expiry(self) -> None:
        from emporos.options.entry import MonthlyExpiries

        assert MonthlyExpiries({}, 21, 49).choose(self.snap(34)) is None

    def test_the_window_must_be_ordered(self) -> None:
        from emporos.options.entry import MonthlyExpiries

        with pytest.raises(ValueError):
            MonthlyExpiries({}, 30, 20)


class TestLotPerExpiry:
    def test_an_expiry_may_override_the_snapshots_lot_size(self) -> None:
        near, far = days_after(30), days_after(60)
        snap = ChainSnapshot(
            DAY0, "T", D(100), 40, D(10), TICK,
            {near: ExpiryChain(near, {}), far: ExpiryChain(far, {}, 25)},
        )  # fmt: skip

        assert (snap.lot_for(near), snap.lot_for(far), snap.lot_for(days_after(9))) == (40, 25, 40)
