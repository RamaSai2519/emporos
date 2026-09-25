"""EM-233: the fixed-weight buy-and-hold: trades only the difference, whole units, net of costs."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.unit.research.swing.support import Always, dataset, free_costs, series, sessions

from emporos.research.swing.costs import BENCHMARK, SwingCostModel
from emporos.research.swing.weighted import WeightedBuyHold

DAYS = sessions(10)
X, Y = "NSE:1", "NSE:2"


def book(
    x: list[tuple[str, str]], y: list[tuple[str, str]], weights: tuple[str, str] = ("0.5", "0.5"),
    rebalance: bool = True, capital: str = "10000", costs=None, cash_yield: str = "0",  # type: ignore[no-untyped-def]
) -> WeightedBuyHold:  # fmt: skip
    data = dataset(series(X, DAYS, x), series(Y, DAYS, y))
    return WeightedBuyHold(
        data, {X: Decimal(weights[0]), Y: Decimal(weights[1])}, costs or free_costs(),
        Always(rebalance), Decimal(capital), Decimal(cash_yield),
    )  # fmt: skip


FLAT = [("100", "100")] * 10


class TestRebalancing:
    def test_the_first_purchase_is_at_the_second_open_in_the_target_weights(self) -> None:
        run = book(FLAT, FLAT, rebalance=False).run()

        assert run.equity[0] == 10000  # nothing yet: decided at the first close
        assert run.equity[1] == 10000  # bought 50 and 50 units at 100: no cost, no change
        assert run.invested_flags == (False, *([True] * 8), False)  # sold at the last close

    def test_a_rebalance_trades_only_the_difference_and_conserves_value_when_costs_are_zero(
        self,
    ) -> None:
        # X jumps 10% at the open of session 3; the rebalance decided at the session-2 close is
        # traded at that open: equity 10,500, target 5,250 each; X holds 5,500: sell 2 units (220);
        # Y is 250 short: buy 2 units (200) with the 220 raised; 20 is left in cash
        x = [("100", "100")] * 3 + [("110", "110")] * 7
        run = book(x, FLAT).run()

        assert run.equity[3] == 10500
        assert run.equity[2] == 10000

    def test_without_a_rebalance_the_weights_drift(self) -> None:
        x = [("100", "100")] * 3 + [("110", "110")] * 7

        run = book(x, FLAT, rebalance=False).run()

        assert run.equity[-1] == 10000 + 50 * 10  # 50 units of X gained 10 each, no trading

    def test_weights_below_one_leave_the_rest_in_cash_that_accrues(self) -> None:
        run = book(FLAT, FLAT, weights=("0.3", "0.3"), rebalance=False, cash_yield="0.05").run()

        g = Decimal("1.05") ** (Decimal(1) / 252)
        # cash of 10,000 grows one session, 30 + 30 units (6,000) are bought at the second open, and
        # the rest (10,000 g - 6,000) accrues for 8 more sessions; the 6,000 of units is sold at the
        # last close at 100 again
        expected = Decimal(6000) + (Decimal(10000) * g - 6000) * g**8
        assert run.equity[-1] == pytest.approx(expected, rel=Decimal("0.0000001"))

    def test_everything_is_sold_at_the_last_close_net_of_costs(self) -> None:
        costs = SwingCostModel(_delivery(), BENCHMARK)

        gross = book(FLAT, FLAT, rebalance=False).run()
        net = book(FLAT, FLAT, rebalance=False, costs=costs).run()

        assert gross.equity[-1] == 10000
        assert net.equity[-1] < 10000 - 20  # buys and the final sells both paid
        assert net.fees > 0

    def test_whole_units_only(self) -> None:
        run = book(
            [("300", "300")] * 10, [("300", "300")] * 10, capital="1000", rebalance=False
        ).run()

        assert run.equity[1] == 1000  # 500 each buys 1 unit of 300; 400 stays cash, no loss


class TestValidation:
    @pytest.mark.parametrize(
        "weights", [{}, {X: Decimal(0)}, {X: Decimal("0.7"), Y: Decimal("0.7")}]
    )
    def test_the_weights_are_positive_and_at_most_one(self, weights: dict[str, Decimal]) -> None:
        data = dataset(series(X, DAYS, FLAT), series(Y, DAYS, FLAT))

        with pytest.raises(ValueError, match="weight|at most 1"):
            WeightedBuyHold(data, weights, free_costs(), Always(True), Decimal(1000))

    def test_an_asset_without_bars_is_refused(self) -> None:
        data = dataset(series(X, DAYS, FLAT))

        with pytest.raises(ValueError, match="no bars"):
            WeightedBuyHold(
                data,
                {X: Decimal("0.5"), Y: Decimal("0.5")},
                free_costs(),
                Always(True),
                Decimal(1000),
            )


def _delivery():  # type: ignore[no-untyped-def]
    from tests.unit.research.swing.test_simulator import _delivery as real

    return real()


def test_the_book_can_start_later_than_the_data_and_starts_from_its_capital() -> None:
    data = dataset(series(X, DAYS, FLAT), series(Y, DAYS, FLAT))
    weights = {X: Decimal("0.5"), Y: Decimal("0.5")}

    run = WeightedBuyHold(
        data, weights, free_costs(), Always(False), Decimal(10000), start_day=DAYS[4]
    ).run()

    assert run.days == tuple(DAYS[4:])
    assert run.equity[0] == 10000
    assert run.invested_flags[0] is False  # the first purchase is at the start's second open


class Scheduled:
    """A weights policy that answers from a script by decision day (None: no decision)."""

    def __init__(self, answers: dict[int, dict[str, Decimal] | None]) -> None:
        self._answers = answers
        self._n = 0

    def targets(self, view):  # type: ignore[no-untyped-def]
        answer = self._answers.get(self._n)
        self._n += 1
        return answer


class TestTargetWeightBook:
    def _book(self, x, y, policy, fill=None, decide_at_start=True):  # type: ignore[no-untyped-def]
        from emporos.research.swing.simulator import FillPrice
        from emporos.research.swing.weighted import TargetWeightBook

        data = dataset(series(X, DAYS, x), series(Y, DAYS, y))
        return TargetWeightBook(
            data, policy, (X, Y), free_costs(), Always(True), Decimal(10000), Decimal(0), None,
            fill or FillPrice.OPEN, decide_at_start,
        )  # fmt: skip

    def test_it_fills_at_the_next_close_when_asked(self) -> None:
        from emporos.research.swing.simulator import FillPrice

        x = [("100", "100"), ("80", "125")] + [("125", "125")] * 8  # the open is 80, the close 125
        policy = Scheduled({0: {X: Decimal(1)}})

        run = self._book(x, FLAT, policy, FillPrice.CLOSE, decide_at_start=True).run()

        # bought 80 units at the session-2 close (125): value 10,000, marked at that same close
        assert run.equity[1] == 10000
        assert run.equity[-1] == 10000

    def test_a_name_left_out_of_the_new_targets_is_sold_and_its_cash_waits(self) -> None:
        x = [("100", "100")] * 10
        policy = Scheduled({0: {X: Decimal(1)}, 1: {Y: Decimal("0.5")}})

        run = self._book(x, FLAT, policy).run()

        # session 1 buys X with everything; session 2 sells X and buys Y with half the book
        assert run.equity == (Decimal(10000),) * 10
        assert run.invested_flags[2] is True

    def test_no_decision_leaves_the_book_as_it_is(self) -> None:
        x = [("100", "100")] * 4 + [("120", "120")] * 6
        policy = Scheduled({0: {X: Decimal(1)}})  # every later answer is None

        run = self._book(x, FLAT, policy).run()

        assert run.equity[-1] == 12000  # still all X: the later None decisions changed nothing

    def test_without_decide_at_start_the_first_question_is_the_first_calendar_rebalance(
        self,
    ) -> None:
        # Always(True) says every session is a rebalance, so the first answer is asked at close 1
        # either way; with a policy that answers only on its second question, the book is in cash
        # until then
        policy = Scheduled({1: {X: Decimal(1)}})

        run = self._book(FLAT, FLAT, policy, decide_at_start=False).run()

        assert run.invested_flags[:3] == (False, False, True)
