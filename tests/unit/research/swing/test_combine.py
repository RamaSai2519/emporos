"""EM-233: a book of sleeves: the split, the quarterly reset and its cost, the scaled trades."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from tests.unit.research.swing.support import ZERO_SCHEDULE, free_costs

from emporos.research.swing.combine import (
    MonthlyCorrelation,
    SleeveBook,
    SleeveCombiner,
    SleeveRun,
    TransferCost,
    pearson,
    quarter_of,
)
from emporos.research.swing.simulator import SwingRun, Trade

D = Decimal
HALF = (D("0.5"), D("0.5"))
NO_COST = TransferCost(D(0))


def sleeve(
    name: str, days: list[date], equity: list[str], capital: str = "5000",
    trades: tuple[Trade, ...] = (), flags: tuple[bool, ...] | None = None,
) -> SleeveRun:  # fmt: skip
    f = flags if flags is not None else (True,) * len(days)
    return SleeveRun(
        name,
        SwingRun(tuple(days), tuple(D(e) for e in equity), sum(f), trades, D(capital), D(0), 0, f),
    )


def trade(name: str, exit_day: date, pnl: str) -> Trade:
    return Trade(name, exit_day, exit_day, D(100), D(101), 10, D("1"), D(pnl))


Q1 = [date(2026, 3, 30), date(2026, 3, 31)]
Q2 = [date(2026, 4, 1), date(2026, 4, 2)]
DAYS = Q1 + Q2


class TestQuarters:
    def test_a_quarter_is_a_calendar_quarter(self) -> None:
        assert quarter_of(date(2026, 3, 31)) == (2026, 0)
        assert quarter_of(date(2026, 4, 1)) == (2026, 1)
        assert quarter_of(date(2026, 12, 31)) != quarter_of(date(2027, 1, 1))


class TestTransferCost:
    def test_the_round_trip_of_a_reference_order_per_rupee_moved(self) -> None:
        cost = TransferCost.of(free_costs())

        assert cost.rate == 0  # no fees, no slippage

    def test_slippage_is_paid_on_both_legs(self) -> None:
        from emporos.research.swing.costs import BENCHMARK, SwingCostModel

        rate = TransferCost.of(SwingCostModel(ZERO_SCHEDULE, BENCHMARK)).rate

        assert rate == pytest.approx(D("0.002"))  # 10 bps a side

    def test_a_negative_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not negative"):
            TransferCost(D(-1))


class TestCombiner:
    def test_the_book_starts_at_the_split_and_drifts_within_a_quarter(self) -> None:
        a = sleeve("a", DAYS, ["5000", "5500", "5500", "5500"])
        b = sleeve("b", DAYS, ["5000", "5000", "5000", "5000"])

        book = SleeveCombiner(HALF, NO_COST, D(10000)).combine([a, b]).run

        assert book.equity[:2] == (D(10000), D(10500))

    def test_the_first_session_of_a_new_quarter_restores_the_split_before_its_move(self) -> None:
        a = sleeve("a", DAYS, ["5000", "6000", "6000", "6600"])  # +20% then flat then +10%
        b = sleeve("b", DAYS, ["5000", "5000", "5000", "5000"])

        combined = SleeveCombiner(HALF, NO_COST, D(10000)).combine([a, b])

        # 2026-03-31: a 6000 + b 5000 = 11000; on 04-01 the split is restored (5500/5500) at the
        # previous close and a's flat day changes nothing; on 04-02 a is +10%
        assert combined.run.equity == (D(10000), D(11000), D(11000), D(11550))
        assert [t.day for t in combined.transfers] == [date(2026, 4, 1)]
        assert combined.transfers[0].moved == D(500)

    def test_a_reset_costs_the_rate_on_the_rupees_moved(self) -> None:
        a = sleeve("a", DAYS, ["5000", "6000", "6000", "6000"])
        b = sleeve("b", DAYS, ["5000", "5000", "5000", "5000"])

        combined = SleeveCombiner(HALF, TransferCost(D("0.01")), D(10000)).combine([a, b])

        assert combined.transfers[0].cost == D(5)  # 500 moved at 1%
        assert combined.transfer_cost == D(5)
        assert combined.run.equity[2] == D(11000) - 5

    def test_no_reset_is_charged_without_a_quarter_change(self) -> None:
        a = sleeve("a", Q1, ["5000", "6000"])
        b = sleeve("b", Q1, ["5000", "5000"])

        combined = SleeveCombiner(HALF, TransferCost(D("0.01")), D(10000)).combine([a, b])

        assert combined.transfers == ()

    def test_only_the_sessions_both_sleeves_have_are_in_the_book(self) -> None:
        a = sleeve("a", DAYS, ["5000", "5100", "5200", "5300"])
        b = sleeve("b", [DAYS[0], DAYS[2], DAYS[3]], ["5000", "5000", "5000"])

        combined = SleeveCombiner(HALF, NO_COST, D(10000)).combine([a, b])

        assert combined.run.days == (DAYS[0], DAYS[2], DAYS[3])
        assert combined.sleeve_days_dropped == 1
        assert combined.run.equity[1] == D(10200)  # a's move over the session it did not share

    def test_invested_when_either_sleeve_is(self) -> None:
        a = sleeve("a", Q1, ["5000", "5000"], flags=(False, False))
        b = sleeve("b", Q1, ["5000", "5000"], flags=(False, True))

        run = SleeveCombiner(HALF, NO_COST, D(10000)).combine([a, b]).run

        assert run.invested_flags == (False, True)
        assert run.days_in_cash == 1

    def test_a_trade_is_scaled_to_the_sleeves_value_in_the_book(self) -> None:
        # a's book value is 1.1 x its own run's on the exit day (the sleeve doubled its weight)
        a = sleeve("a", Q1, ["5000", "5000"], trades=(trade("X", Q1[1], "100"),))
        b = sleeve("b", Q1, ["5000", "5000"])
        combined = SleeveCombiner((D("0.55"), D("0.45")), NO_COST, D(10000)).combine([a, b])

        scaled = combined.run.trades[0]

        assert scaled.net_pnl == D(110)  # 100 x (5500 / 5000)
        assert scaled.fees == D("1.1")
        assert scaled.quantity == 10  # the run's own count stays

    def test_the_weights_are_checked(self) -> None:
        with pytest.raises(ValueError, match="add up to 1"):
            SleeveCombiner((D("0.6"), D("0.6")), NO_COST, D(10000))
        with pytest.raises(ValueError, match="positive"):
            SleeveCombiner((D(1), D(0)), NO_COST, D(10000))
        with pytest.raises(ValueError, match="capital"):
            SleeveCombiner(HALF, NO_COST, D(0))

    def test_one_weight_per_sleeve(self) -> None:
        a = sleeve("a", Q1, ["5000", "5000"])

        with pytest.raises(ValueError, match="one weight per sleeve"):
            SleeveCombiner(HALF, NO_COST, D(10000)).combine([a])

    def test_sleeves_with_no_common_session_are_refused(self) -> None:
        a = sleeve("a", Q1, ["5000", "5000"])
        b = sleeve("b", Q2, ["5000", "5000"])

        with pytest.raises(ValueError, match="no session in common"):
            SleeveCombiner(HALF, NO_COST, D(10000)).combine([a, b])


class TestSleeveBook:
    def test_it_decides_nothing_itself(self) -> None:
        assert list(SleeveBook({}).desired(None)) == []  # type: ignore[arg-type]


class TestCorrelation:
    def test_perfectly_correlated_and_anticorrelated_series(self) -> None:
        assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
        assert pearson([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)

    def test_a_flat_series_has_no_correlation(self) -> None:
        assert pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
        assert pearson([1.0], [1.0]) is None

    def test_series_of_different_length_are_refused(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            pearson([1.0], [1.0, 2.0])

    def test_the_worst_months_of_the_first_sleeve_and_what_the_second_did_in_them(self) -> None:
        first = {(2026, m): r for m, r in enumerate([0.05, -0.10, 0.02, -0.05, 0.03], 1)}
        second = {(2026, m): r for m, r in enumerate([0.00, 0.04, 0.01, 0.02, -0.01], 1)}

        c = MonthlyCorrelation.of(first, second, worst_n=2)

        assert c.months == 5
        assert c.worst_months == ((2026, 2), (2026, 4))
        assert c.first_mean_in_worst == pytest.approx(-0.075)
        assert c.second_mean_in_worst == pytest.approx(0.03)
        assert c.overall is not None and c.overall < 0

    def test_a_month_only_one_sleeve_has_is_left_out(self) -> None:
        c = MonthlyCorrelation.of({(2026, 1): 0.1, (2026, 2): 0.2}, {(2026, 2): 0.1}, worst_n=1)

        assert c.months == 1

    def test_sleeves_that_share_no_month_are_refused(self) -> None:
        with pytest.raises(ValueError, match="share no month"):
            MonthlyCorrelation.of({(2026, 1): 0.1}, {(2026, 2): 0.1})
