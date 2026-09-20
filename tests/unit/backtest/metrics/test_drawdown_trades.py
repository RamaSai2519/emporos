"""EM-105: drawdowns and trade statistics on series worked by hand."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import ClassVar

from emporos.backtest.metrics.drawdown import DrawdownAnalyzer
from emporos.backtest.metrics.trades import TradeAnalyzer
from tests.support.backtest_metrics import curve, point, trade
from tests.support.strategies import T0

D = Decimal
DAY = timedelta(days=1)


class TestDrawdown:
    def test_a_recovered_and_an_open_drawdown_worked_by_hand(self) -> None:
        # the account starts at 100. Path: 120, 90, 110, 130, 65, 70 (one point a day, t1..t6)
        #   peak 120 (t1) -> trough 90 (t2), 110 (t3) still under; back above 120 at t4:
        #     recovered, depth (120 - 90) / 120 = 0.25, peak to recovery = 3 days
        #   peak 130 (t4) -> trough 65 (t5), 70 (t6): never recovered, depth (130 - 65) / 130 = 0.5
        report = DrawdownAnalyzer().analyze(D(100), curve(["120", "90", "110", "130", "65", "70"]))

        assert report.max_drawdown == D("0.5")
        assert report.count == 2
        deepest, shallower = report.periods
        assert (deepest.depth, deepest.recovered_at) == (D("0.5"), None)
        assert (deepest.peak_at, deepest.trough_at) == (T0 + 4 * DAY, T0 + 5 * DAY)
        assert (shallower.depth, shallower.recovered_at) == (D("0.25"), T0 + 4 * DAY)
        assert (shallower.peak_at, shallower.trough_at) == (T0 + 1 * DAY, T0 + 2 * DAY)
        assert report.longest_days == 3  # the recovered one; the open one has lasted only 2

    def test_an_open_drawdown_can_be_the_longest(self) -> None:
        report = DrawdownAnalyzer().analyze(D(100), curve(["110", "100", "99", "98", "97", "96"]))
        assert report.longest_days == 5  # from the peak at t1 to the last point at t6

    def test_a_curve_that_only_rises_has_no_drawdown(self) -> None:
        report = DrawdownAnalyzer().analyze(D(100), curve(["101", "102", "103"]))
        assert (report.max_drawdown, report.count, report.periods, report.longest_days) == (
            0,
            0,
            (),
            0,
        )

    def test_equity_back_at_the_peak_counts_as_recovered(self) -> None:
        report = DrawdownAnalyzer().analyze(D(100), curve(["110", "100", "110"]))
        (period,) = report.periods
        assert period.recovered_at == T0 + 3 * DAY

    def test_the_starting_cash_is_the_first_peak(self) -> None:
        report = DrawdownAnalyzer().analyze(D(100), curve(["90", "95", "100"]))
        (period,) = report.periods
        assert (period.peak_equity, period.trough_equity, period.depth) == (D(100), D(90), D("0.1"))
        assert period.peak_at == T0 + 1 * DAY  # anchored at the first point on the curve
        assert period.recovered_at == T0 + 3 * DAY

    def test_an_intraday_plunge_that_recovers_by_the_close_is_seen(self) -> None:
        minute = timedelta(minutes=5)
        report = DrawdownAnalyzer().analyze(
            D(100),
            curve(["100", "80", "100"], step=minute),  # same day, three bars
        )
        assert report.max_drawdown == D("0.2")

    def test_only_the_five_deepest_are_listed_but_all_are_counted(self) -> None:
        equities: list[str] = []
        for depth in range(1, 8):  # seven dips of increasing depth, each followed by a new high
            peak = 100 + 10 * depth
            equities += [str(peak), str(peak - depth), str(peak + 1)]
        report = DrawdownAnalyzer().analyze(D(100), curve(equities))

        assert report.count == 7 and len(report.periods) == 5
        depths = [p.depth for p in report.periods]
        assert depths == sorted(depths, reverse=True)

    def test_no_curve(self) -> None:
        assert DrawdownAnalyzer().analyze(D(100), []).max_drawdown == 0

    def test_a_single_point_below_the_start(self) -> None:
        report = DrawdownAnalyzer().analyze(D(100), [point("90")])
        assert report.max_drawdown == D("0.1") and report.periods[0].recovered_at is None


class TestTradeStatistics:
    # net P&L of nine trades, in the order they closed; each entered 1,000 (10 @ 100)
    NETS: ClassVar[list[str]] = ["100", "-50", "200", "50", "-25", "-25", "0", "10", "10"]

    def trades(self):  # type: ignore[no-untyped-def]
        return [trade(net, minute=10 * n) for n, net in enumerate(self.NETS)]

    def test_the_headline_numbers_by_hand(self) -> None:
        stats = TradeAnalyzer().analyze(self.trades())

        assert (stats.count, stats.wins, stats.losses, stats.breakeven) == (9, 5, 3, 1)
        assert abs(stats.win_rate - D(5) / D(9)) < D("1e-25")
        assert stats.gross_profit.amount == 370  # 100 + 200 + 50 + 10 + 10
        assert stats.gross_loss.amount == 100  # 50 + 25 + 25
        assert stats.profit_factor == D("3.7")
        assert stats.net_pnl.amount == 270
        assert stats.average_trade.amount == 30  # 270 / 9
        assert stats.average_win.amount == 74  # 370 / 5
        assert abs(stats.average_loss.amount - D(-100) / 3) < D("1e-25")
        assert stats.expectancy == D("0.03")  # mean of net / 1,000 = 30 / 1,000

    def test_streaks_follow_the_order_trades_closed_and_a_breakeven_breaks_both(self) -> None:
        # W L W W L L B W W : the longest runs are 2 wins (200, 50 or 10, 10) and 2 losses
        stats = TradeAnalyzer().analyze(self.trades())
        assert (stats.max_consecutive_wins, stats.max_consecutive_losses) == (2, 2)

    def test_the_order_given_does_not_matter_only_the_order_they_closed(self) -> None:
        shuffled = list(reversed(self.trades()))
        assert TradeAnalyzer().analyze(shuffled) == TradeAnalyzer().analyze(self.trades())

    def test_a_breakeven_between_wins_splits_the_streak(self) -> None:
        trades = [trade(n, minute=10 * i) for i, n in enumerate(["5", "5", "0", "5", "5", "5"])]
        assert TradeAnalyzer().analyze(trades).max_consecutive_wins == 3

    def test_gross_pnl_fees_and_net_are_kept_apart(self) -> None:
        stats = TradeAnalyzer().analyze([trade("90", 0, fees="10"), trade("-30", 10, fees="10")])
        # gross = net + fees: 100 and -20 -> 80; fees 10 + 10 -> 20; net 90 - 30 -> 60
        assert (stats.gross_pnl.amount, stats.fees.amount, stats.net_pnl.amount) == (80, 20, 60)

    def test_a_trade_that_only_loses_after_charges_is_a_loss(self) -> None:
        stats = TradeAnalyzer().analyze([trade("-4", 0, fees="5")])  # gross +1, fees 5
        assert (stats.wins, stats.losses) == (0, 1)

    def test_no_losing_trade_means_no_profit_factor(self) -> None:
        stats = TradeAnalyzer().analyze([trade("10", 0), trade("20", 10)])
        assert stats.profit_factor is None and stats.average_loss is None
        assert stats.win_rate == D(1)

    def test_no_trades_means_nothing_is_claimed(self) -> None:
        stats = TradeAnalyzer().analyze([])
        assert stats.count == 0
        assert (stats.win_rate, stats.profit_factor, stats.average_trade, stats.expectancy) == (
            None, None, None, None,
        )  # fmt: skip
        assert (stats.max_consecutive_wins, stats.net_pnl.amount) == (0, 0)

    def test_expectancy_is_a_return_on_the_notional_not_a_money_amount(self) -> None:
        big = trade("100", 0, quantity=100, price="100")  # entered 10,000 -> +1%
        small = trade("100", 10, quantity=10, price="100")  # entered 1,000 -> +10%
        assert TradeAnalyzer().analyze([big, small]).expectancy == D("0.055")
