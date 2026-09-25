"""EM-240: the risk engine: sizing from the stop and every rule of PROFIT_PLAN §12.4."""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from emporos.core.clock import IST
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.risk.limits import RiskLimits
from emporos.eventtrader.risk.models import (
    EntryProposal,
    OpenPosition,
    Product,
    Refusal,
    RiskDecision,
    RiskSnapshot,
    Sized,
)
from emporos.eventtrader.risk.rules import StopUpdateGuard
from emporos.eventtrader.stages.models import Side

D = Decimal
MONDAY_11 = datetime(2024, 3, 4, 11, 0, tzinfo=IST)
ENGINE = RiskEngine()


def snapshot(**overrides: object) -> RiskSnapshot:
    values: dict[str, object] = {
        "now": MONDAY_11, "starting_capital": D(100000), "realized_total": D(0),
        "realized_today": D(0), "open_positions": (), "posture_scale": D(1),
    }  # fmt: skip
    return RiskSnapshot(**{**values, **overrides})  # type: ignore[arg-type]


def proposal(**overrides: object) -> EntryProposal:
    values: dict[str, object] = {
        "name": "NSE:2885", "product": Product.INTRADAY, "side": Side.LONG, "entry_price": D(1000),
        "stop_price": D(980), "at": MONDAY_11,
    }  # fmt: skip
    return EntryProposal(**{**values, **overrides})  # type: ignore[arg-type]


def held(
    name: str = "NSE:1", product: Product = Product.INTRADAY, risk: int = 1000, **kw: object
) -> OpenPosition:
    values: dict[str, object] = {
        "name": name, "product": product, "side": Side.LONG, "quantity": 10,
        "entry_price": D(100), "stop_price": D(90), "risk": D(risk),
    }  # fmt: skip
    return OpenPosition(**{**values, **kw})  # type: ignore[arg-type]


def rules_of(decision: RiskDecision) -> set[str]:
    return {r.rule for r in decision.refusals}


class TestLimits:
    def test_the_declared_numbers_are_the_operators(self) -> None:
        limits = RiskLimits()

        assert (limits.cash_risk_per_trade, limits.option_premium_per_trade) == (D(2000), D(5000))
        assert (limits.daily_loss, limits.total_loss, limits.open_risk) == (
            D(5000),
            D(25000),
            D(25000),
        )
        assert (limits.intraday_position_value, limits.swing_position_value) == (D(50000), D(25000))
        assert (limits.intraday_stop_pct, limits.swing_stop_pct) == (D(5), D(12))
        assert (limits.max_intraday_positions, limits.max_swing_positions) == (3, 4)
        assert (limits.no_intraday_entry_after, limits.square_off_at) == (
            time(14, 45),
            time(15, 15),
        )


class TestSizing:
    def test_cash_quantity_is_the_risk_budget_over_the_stop_distance(self) -> None:
        decision = ENGINE.review(proposal(entry_price=D(1000), stop_price=D(950)), snapshot())

        assert decision.approved and decision.sized is not None
        assert (decision.sized.quantity, decision.sized.risk, decision.sized.position_value) == (
            40,
            D(2000),
            D(40000),
        )

    def test_the_quantity_rounds_down_never_up(self) -> None:
        sized = ENGINE.size(proposal(entry_price=D(500), stop_price=D(470)), snapshot())  # 2000/30

        assert isinstance(sized, Sized) and sized.quantity == 66 and sized.risk == D(1980)

    def test_the_position_value_cap_binds_before_the_risk_budget(self) -> None:
        sized = ENGINE.size(
            proposal(entry_price=D(500), stop_price=D(495)), snapshot()
        )  # 2000/5 = 400

        assert (
            isinstance(sized, Sized) and sized.quantity == 100 and sized.position_value == D(50000)
        )
        assert sized.risk == D(500)

    def test_a_swing_position_is_capped_at_twenty_five_thousand(self) -> None:
        sized = ENGINE.size(
            proposal(product=Product.SWING, entry_price=D(500), stop_price=D(490)), snapshot()
        )

        assert (
            isinstance(sized, Sized) and sized.quantity == 50 and sized.position_value == D(25000)
        )

    def test_a_short_is_sized_from_a_stop_above_the_entry(self) -> None:
        sized = ENGINE.size(
            proposal(side=Side.SHORT, entry_price=D(1000), stop_price=D(1040)), snapshot()
        )

        assert isinstance(sized, Sized) and sized.quantity == 50 and sized.risk == D(2000)

    def test_a_stop_so_wide_that_not_one_share_fits_is_a_refusal_not_a_zero(self) -> None:
        sized = ENGINE.size(proposal(entry_price=D(60000), stop_price=D(57000)), snapshot())

        assert isinstance(sized, Refusal) and "one share" in sized.reason

    def test_an_option_is_sized_in_whole_lots_that_fit_the_premium_budget(self) -> None:
        cheap = ENGINE.size(
            proposal(product=Product.OPTION, entry_price=D(20), stop_price=None, lot_size=50),
            snapshot(),
        )
        exact = ENGINE.size(
            proposal(product=Product.OPTION, entry_price=D(50), stop_price=None, lot_size=100),
            snapshot(),
        )
        dear = ENGINE.size(
            proposal(product=Product.OPTION, entry_price=D(60), stop_price=None, lot_size=100),
            snapshot(),
        )

        assert isinstance(cheap, Sized) and (cheap.quantity, cheap.risk) == (250, D(5000))
        assert isinstance(exact, Sized) and (exact.quantity, exact.risk) == (100, D(5000))
        assert isinstance(dear, Refusal) and "over the premium budget" in dear.reason

    def test_the_posture_scales_the_budget_but_never_above_the_cap(self) -> None:
        normal = ENGINE.size(
            proposal(entry_price=D(1000), stop_price=D(950)), snapshot(posture_scale=D("0.6"))
        )
        greedy = ENGINE.size(
            proposal(entry_price=D(1000), stop_price=D(950)), snapshot(posture_scale=D(3))
        )

        assert isinstance(normal, Sized) and normal.risk == D(1200)
        assert isinstance(greedy, Sized) and greedy.risk == D(
            2000
        )  # never above the Rs 2,000 budget

    def test_a_hold_posture_refuses_everything(self) -> None:
        decision = ENGINE.review(proposal(), snapshot(posture_scale=D(0)))

        assert not decision.approved and rules_of(decision) == {"posture_hold"}


class TestRules:
    def test_a_clean_proposal_is_approved_with_a_size(self) -> None:
        decision = ENGINE.review(proposal(), snapshot())

        assert decision.approved and decision.sized is not None and decision.refusals == ()

    def test_the_stop_width_limits_are_five_percent_intraday_and_twelve_swing(self) -> None:
        at_limit = ENGINE.review(proposal(entry_price=D(1000), stop_price=D(950)), snapshot())
        over = ENGINE.review(proposal(entry_price=D(1000), stop_price=D("949.9")), snapshot())
        swing_ok = ENGINE.review(
            proposal(product=Product.SWING, entry_price=D(1000), stop_price=D(880)), snapshot()
        )
        swing_over = ENGINE.review(
            proposal(product=Product.SWING, entry_price=D(1000), stop_price=D(879)), snapshot()
        )

        assert at_limit.approved and rules_of(over) == {"stop_width"}
        assert swing_ok.approved and rules_of(swing_over) == {"stop_width"}

    def test_a_stop_on_the_wrong_side_or_missing_is_refused(self) -> None:
        assert "stop_side" in rules_of(ENGINE.review(proposal(stop_price=D(1010)), snapshot()))
        assert "stop_side" in rules_of(
            ENGINE.review(proposal(side=Side.SHORT, stop_price=D(990)), snapshot())
        )
        missing = ENGINE.review(proposal(stop_price=None), snapshot())
        assert not missing.approved and rules_of(missing) == {"sizing"}

    @pytest.mark.parametrize(
        ("hour", "minute", "ok"),
        [(14, 44, True), (14, 45, False), (15, 20, False), (9, 14, False), (9, 15, True)],
    )
    def test_no_intraday_entry_from_1445_and_none_before_the_open(
        self, hour: int, minute: int, ok: bool
    ) -> None:
        at = datetime(2024, 3, 4, hour, minute, tzinfo=IST)

        decision = ENGINE.review(proposal(at=at), snapshot(now=at))

        assert decision.approved is ok and (ok or rules_of(decision) == {"entry_window"})

    def test_the_entry_window_reads_ist_whatever_zone_the_time_is_in(self) -> None:
        at = datetime(2024, 3, 4, 9, 30, tzinfo=UTC)  # 15:00 IST

        assert "entry_window" in rules_of(ENGINE.review(proposal(at=at), snapshot(now=at)))

    def test_a_swing_entry_at_the_close_is_not_an_intraday_entry(self) -> None:
        at = datetime(2024, 3, 4, 15, 29, tzinfo=IST)

        assert ENGINE.review(proposal(product=Product.SWING, at=at), snapshot(now=at)).approved

    def test_the_weekend_is_closed(self) -> None:
        at = datetime(2024, 3, 2, 11, 0, tzinfo=IST)  # a Saturday

        assert "entry_window" in rules_of(ENGINE.review(proposal(at=at), snapshot(now=at)))

    def test_one_position_per_name_so_no_averaging_down(self) -> None:
        decision = ENGINE.review(proposal(name="NSE:1"), snapshot(open_positions=(held("NSE:1"),)))

        assert rules_of(decision) == {"one_position_per_name"}

    def test_at_most_three_intraday_and_four_swing_positions(self) -> None:
        three = tuple(held(f"NSE:{i}") for i in range(3))
        four = tuple(held(f"NSE:{i}", Product.SWING, 500) for i in range(4))

        assert "max_positions" in rules_of(
            ENGINE.review(proposal(name="NSE:9"), snapshot(open_positions=three))
        )
        assert ENGINE.review(
            proposal(name="NSE:9", product=Product.SWING), snapshot(open_positions=three)
        ).approved
        assert "max_positions" in rules_of(
            ENGINE.review(
                proposal(name="NSE:9", product=Product.SWING), snapshot(open_positions=four)
            )
        )

    def test_an_open_option_counts_toward_the_swing_limit(self) -> None:
        book = (
            held("NSE:1", Product.SWING, 500), held("NSE:2", Product.SWING, 500),
            held("NSE:3", Product.OPTION, 500), held("NSE:4", Product.OPTION, 500),
        )  # fmt: skip

        assert "max_positions" in rules_of(
            ENGINE.review(
                proposal(name="NSE:9", product=Product.SWING), snapshot(open_positions=book)
            )
        )

    def test_open_risk_can_never_pass_twenty_five_thousand(self) -> None:
        book = (held("NSE:1", Product.SWING, 12000), held("NSE:2", Product.SWING, 12000))

        decision = ENGINE.review(
            proposal(entry_price=D(1000), stop_price=D(950)), snapshot(open_positions=book)
        )

        assert rules_of(decision) == {"open_risk_cap"}  # 24,000 + 2,000 > 25,000

    def test_a_daily_loss_of_five_thousand_realised_plus_open_marked_halts_entries(self) -> None:
        losing = (held("NSE:1", risk=2000, marked_pnl=D(-1500)),)

        halted = ENGINE.review(
            proposal(name="NSE:9"), snapshot(realized_today=D(-3500), open_positions=losing)
        )
        fine = ENGINE.review(
            proposal(name="NSE:9"), snapshot(realized_today=D(-3499), open_positions=losing)
        )

        assert rules_of(halted) == {"daily_loss_halt"} and fine.approved

    def test_an_open_loss_is_counted_at_its_stop_not_beyond_it(self) -> None:
        gapped = (held("NSE:1", risk=2000, marked_pnl=D(-9000)),)  # marked far past a Rs 2,000 stop

        assert ENGINE.review(
            proposal(name="NSE:9"), snapshot(realized_today=D(-2000), open_positions=gapped)
        ).approved
        assert "daily_loss_halt" in rules_of(
            ENGINE.review(
                proposal(name="NSE:9"), snapshot(realized_today=D(-3000), open_positions=gapped)
            )
        )

    def test_open_profit_offsets_a_realised_loss(self) -> None:
        winning = (held("NSE:1", risk=2000, marked_pnl=D(3000)),)

        assert ENGINE.review(
            proposal(name="NSE:9"), snapshot(realized_today=D(-5500), open_positions=winning)
        ).approved

    def test_the_daily_halt_forgets_yesterday(self) -> None:
        assert ENGINE.review(
            proposal(), snapshot(realized_today=D(0), realized_total=D(-8000))
        ).approved

    def test_a_total_loss_of_twenty_five_thousand_stops_the_track(self) -> None:
        realised = snapshot(realized_total=D(-25000))
        marked = snapshot(realized_total=D(-20000), open_positions=(held(marked_pnl=D(-5000)),))
        ok = snapshot(realized_total=D(-24999))

        assert "total_loss_kill" in rules_of(ENGINE.review(proposal(), realised))
        assert (
            ENGINE.kill_tripped(realised)
            and ENGINE.kill_tripped(marked)
            and not ENGINE.kill_tripped(ok)
        )

    def test_options_are_only_bought_and_a_swing_short_is_a_put_not_a_short(self) -> None:
        sold = ENGINE.review(
            proposal(
                product=Product.OPTION,
                side=Side.SHORT,
                entry_price=D(20),
                stop_price=None,
                lot_size=50,
            ),
            snapshot(),
        )
        short = ENGINE.review(
            proposal(
                product=Product.SWING, side=Side.SHORT, entry_price=D(1000), stop_price=D(1040)
            ),
            snapshot(),
        )

        assert "no_naked_option_selling" in rules_of(sold) and rules_of(short) == {
            "swing_long_only"
        }

    def test_every_refusal_is_reported_not_just_the_first(self) -> None:
        book = tuple(held(f"NSE:{i}", risk=2000) for i in range(3))
        decision = ENGINE.review(
            proposal(name="NSE:1", stop_price=D(900)),
            snapshot(open_positions=book, realized_today=D(-5000)),
        )

        assert rules_of(decision) >= {
            "stop_width",
            "one_position_per_name",
            "max_positions",
            "daily_loss_halt",
        }


class TestStopsAndEngine:
    def test_a_stop_is_only_ever_tightened(self) -> None:
        long = held(side=Side.LONG, stop_price=D(90))
        short = held(side=Side.SHORT, stop_price=D(110))

        assert StopUpdateGuard.widening(long, D(89)) and not StopUpdateGuard.widening(long, D(95))
        assert StopUpdateGuard.widening(short, D(111)) and not StopUpdateGuard.widening(
            short, D(105)
        )
        assert not StopUpdateGuard.widening(held(stop_price=None), D(1))

    def test_rule_names_are_unique_and_there_is_at_least_one(self) -> None:
        with pytest.raises(ValueError, match="own name"):
            RiskEngine(rules=[])
        rules = RiskEngine().review  # noqa: F841
        from emporos.eventtrader.risk.rules import PostureHold

        with pytest.raises(ValueError, match="own name"):
            RiskEngine(rules=[PostureHold(), PostureHold()])

    def test_the_snapshot_and_decisions_are_immutable_and_consistent(self) -> None:
        s = snapshot()
        with pytest.raises(AttributeError):
            s.realized_today = D(5)  # type: ignore[misc]
        with pytest.raises(ValueError, match="says why"):
            RiskDecision(False)
        with pytest.raises(ValueError, match="carries a size"):
            RiskDecision(True)
        with pytest.raises(ValueError, match="at least one"):
            Sized(0, D(0), D(0))
