"""EM-228: A1's rules, one test per line of the declaration."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.unit.research.swing.support import Always, context, dataset, holding, series, sessions

from emporos.research.swing.momentum import MomentumTrend
from emporos.research.swing.rules import LossStop

DAYS = sessions(40)
NOW = DAYS[35]  # 36 sessions of history: enough for a 5-session lookback (27 needed)


def grow(rate: str, n: int = 40, start: str = "100") -> list[tuple[str, str]]:
    price, out = Decimal(start), []
    for _ in range(n):
        nxt = price * (1 + Decimal(rate))
        out.append((str(price), str(nxt)))
        price = nxt
    return out


def names(**paths: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
    return dataset(*(series(f"NSE:{n}", DAYS[: len(p)], p) for n, p in paths.items()))


def strategy(
    n: int = 2, regime: bool = True, rebalance: bool = True, lookback: int = 5
) -> MomentumTrend:
    return MomentumTrend(lookback, n, Always(rebalance), Always(regime), LossStop())


def picks(s: MomentumTrend, data, *held: str, day=NOW, tradable=None) -> list[str]:  # type: ignore[no-untyped-def]
    ctx = context(data, day, [holding(f"NSE:{h}") for h in held], tradable)
    return [i.instrument_id.removeprefix("NSE:") for i in s.desired(ctx)]


DATA = names(A=grow("0.01"), B=grow("0.005"), C=grow("0.002"), D=grow("-0.003"), E=grow("0"))


class TestRanking:
    def test_it_takes_the_top_n_by_momentum(self) -> None:
        assert picks(strategy(2), DATA) == ["A", "B"]

    def test_a_name_with_momentum_of_zero_or_less_is_not_ranked(self) -> None:
        assert picks(strategy(5), DATA) == ["A", "B", "C"]  # D falls, E is flat

    def test_the_last_month_is_skipped(self) -> None:
        # P rose until 21 sessions ago then collapsed since; Q was flat until 21 sessions ago, then
        # rose. Skipping the last month, P's momentum is strongly positive and Q's is zero.
        p = grow("0.02", 15) + grow("-0.03", 25, start="130")
        q = grow("0", 15) + grow("0.03", 25)
        data = names(P=p, Q=q)

        assert picks(strategy(2), data) == ["P"]

    def test_the_window_is_l_plus_21_sessions_back_from_the_signal_close(self) -> None:
        # with L = 5 the momentum at NOW is close[NOW-21] / close[NOW-26] - 1
        path = grow("0.0", 40)
        path[35 - 26] = ("100", "50")  # close[NOW-26] = 50
        path[35 - 21] = ("100", "100")  # close[NOW-21] = 100: momentum +100%
        data = names(A=path, B=grow("0.01"))

        assert picks(strategy(1), data) == ["A"]

    def test_a_name_without_the_full_window_is_not_ranked(self) -> None:
        short = grow("0.05", 20)  # only 20 sessions: fewer than the 27 needed
        data = names(A=grow("0.001"), S=short)
        s = strategy(2)

        assert picks(s, data, day=DAYS[19]) == []
        assert picks(s, data) == ["A"]

    def test_ties_break_by_instrument_id(self) -> None:
        data = names(B=grow("0.01"), A=grow("0.01"), C=grow("0.01"))

        assert picks(strategy(2), data) == ["A", "B"]

    def test_a_name_that_did_not_trade_today_is_not_ranked(self) -> None:
        assert picks(strategy(2), DATA, tradable=["NSE:B", "NSE:C"]) == ["B", "C"]


class TestHysteresis:
    def test_a_holding_ranked_inside_2n_is_kept_and_the_rest_filled_from_the_top(self) -> None:
        # N = 2: C ranks 3rd (inside the top 4): kept; one slot left, filled by A
        assert picks(strategy(2), DATA, "C") == ["C", "A"]

    def test_a_holding_ranked_outside_2n_is_sold(self) -> None:
        # N = 1: the top 2 are A, B. C ranks 3rd: sold. The book takes A.
        assert picks(strategy(1), DATA, "C") == ["A"]

    def test_a_holding_that_is_no_longer_ranked_is_sold(self) -> None:
        assert picks(strategy(2), DATA, "D") == ["A", "B"]

    def test_the_book_never_exceeds_n_even_if_handed_more_holdings(self) -> None:
        assert len(picks(strategy(2), DATA, "A", "B", "C")) == 2

    def test_kept_holdings_come_first_in_rank_order(self) -> None:
        assert picks(strategy(3), DATA, "C", "B") == ["B", "C", "A"]


class TestBetweenRebalances:
    def test_the_book_is_left_alone(self) -> None:
        s = strategy(2, rebalance=False)
        ctx = context(DATA, NOW, [holding("NSE:C", price="80"), holding("NSE:D", price="80")])

        # even D, which is not ranked and would be sold at a rebalance
        assert [i.instrument_id for i in s.desired(ctx)] == ["NSE:C", "NSE:D"]

    def test_no_new_names_are_bought(self) -> None:
        assert picks(strategy(2, rebalance=False), DATA) == []

    def test_a_holding_at_its_stop_is_sold_even_between_rebalances(self) -> None:
        # entry price 200 against a close near 200 * ... A closes far below the stop price
        s = strategy(2, rebalance=False)
        ctx = context(DATA, NOW, [holding("NSE:A", price="500", value="20000", equity="100000")])

        assert list(s.desired(ctx)) == []
        assert s.record.stops == 1

    def test_a_holding_above_its_stop_stays(self) -> None:
        s = strategy(2, rebalance=False)
        ctx = context(DATA, NOW, [holding("NSE:A", price="100", value="20000", equity="100000")])

        assert [i.instrument_id for i in s.desired(ctx)] == ["NSE:A"]

    def test_a_stopped_name_may_return_at_a_rebalance_if_it_still_ranks(self) -> None:
        s = strategy(2)
        ctx = context(DATA, NOW, [holding("NSE:A", price="500", value="20000", equity="100000")])

        assert [i.instrument_id for i in s.desired(ctx)] == ["NSE:A", "NSE:B"]


class TestRegime:
    def test_off_means_cash(self) -> None:
        assert picks(strategy(2, regime=False), DATA, "A", "B") == []

    def test_a_regime_that_is_off_does_not_even_rank(self) -> None:
        s = strategy(2, regime=False)
        picks(s, DATA)

        assert s.record.first_ranked_day is None


class TestRecord:
    def test_it_notes_the_first_ranked_day_and_the_rebalances(self) -> None:
        s = strategy(2)

        picks(s, DATA, day=DAYS[10])  # too early: nothing ranks
        picks(s, DATA, day=DAYS[30])
        picks(s, DATA, day=DAYS[31])

        assert s.record.first_ranked_day == DAYS[30]
        assert s.record.rebalances == 2

    @pytest.mark.parametrize("kwargs", [{"lookback": 0}, {"max_positions": 0}])
    def test_the_parameters_are_positive(self, kwargs: dict[str, int]) -> None:
        args = {"lookback": 5, "max_positions": 2, **kwargs}
        with pytest.raises(ValueError, match="positive"):
            MomentumTrend(
                args["lookback"], args["max_positions"], Always(True), Always(True), LossStop()
            )
