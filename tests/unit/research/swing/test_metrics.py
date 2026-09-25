"""EM-223: the §3.2 numbers, hand-computed on small equity curves."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from math import sqrt

import pytest

from emporos.research.swing.metrics import SwingMetrics, max_drawdown, period_returns, sharpe
from emporos.research.swing.simulator import SwingRun, Trade

D = Decimal


def run(
    days: list[date], equity: list[str], *, trades: tuple[Trade, ...] = (), invested: int = 0,
    capital: str = "1000",
) -> SwingRun:  # fmt: skip
    return SwingRun(tuple(days), tuple(D(e) for e in equity), invested, trades, D(capital), D(0), 0)


def trade(name: str, pnl: str) -> Trade:
    d = date(2026, 1, 5)
    return Trade(name, d, d, D(1), D(1), 1, D(0), D(pnl))


class TestPeriods:
    def test_monthly_returns_chain_from_the_initial_capital(self) -> None:
        days = [date(2026, 1, 30), date(2026, 2, 2), date(2026, 2, 27), date(2026, 3, 2)]
        equity = [D("1100"), D("1050"), D("1210"), D("1210")]

        months = period_returns(days, equity, D("1000"), by="month")

        assert months == pytest.approx([0.10, 1210 / 1100 - 1, 0.0])

    def test_yearly_returns(self) -> None:
        days = [date(2025, 12, 31), date(2026, 6, 1), date(2026, 12, 31)]

        years = period_returns(days, [D("1100"), D("1000"), D("1320")], D("1000"), by="year")

        assert years == pytest.approx([0.10, 0.20])


class TestDrawdown:
    def test_it_is_the_worst_peak_to_trough_fall(self) -> None:
        assert max_drawdown([D(110), D(90), D(120), D(100)], D(100)) == pytest.approx(20 / 110)

    def test_a_curve_that_only_rises_never_draws_down(self) -> None:
        assert max_drawdown([D(101), D(102)], D(100)) == 0.0

    def test_a_fall_from_the_starting_capital_counts(self) -> None:
        assert max_drawdown([D(90)], D(100)) == pytest.approx(0.10)


class TestSharpe:
    def test_it_is_mean_over_sample_sd_annualised(self) -> None:
        returns = [0.01, -0.01, 0.02]
        mean = 0.02 / 3
        sd = sqrt(sum((r - mean) ** 2 for r in returns) / 2)

        assert sharpe(returns) == pytest.approx(mean / sd * sqrt(252))

    @pytest.mark.parametrize("returns", [[], [0.01], [0.01, 0.01, 0.01]])
    def test_no_spread_no_sharpe(self, returns: list[float]) -> None:
        assert sharpe(returns) is None


class TestStats:
    DAYS = (date(2026, 1, 30), date(2026, 2, 27), date(2026, 3, 31), date(2026, 4, 30))

    def test_a_steady_riser(self) -> None:
        r = run(list(self.DAYS), ["1100", "1210", "1331", "1464.1"], invested=3)

        s = SwingMetrics.of(r)

        assert s.total_return == pytest.approx(0.4641)
        assert s.months == 4
        assert s.positive_month_share == 1.0
        assert s.worst_month == pytest.approx(0.10)
        assert s.max_drawdown == 0.0
        assert s.days == 4
        assert s.days_in_cash == 1
        assert s.net_profit == pytest.approx(464.1)
        assert s.monthly_t is None  # every month returned exactly 10%: no spread

    def test_cagr_compounds_over_the_calendar_span(self) -> None:
        days = [date(2024, 1, 1), date(2026, 1, 1)]
        s = SwingMetrics.of(run(days, ["1210", "1210"], capital="1000"))

        span_years = (date(2026, 1, 1) - date(2024, 1, 1)).days / 365.25
        assert s.net_cagr == pytest.approx(1.21 ** (1 / span_years) - 1)

    def test_a_total_loss_is_a_cagr_of_minus_one(self) -> None:
        s = SwingMetrics.of(run([date(2026, 1, 2), date(2026, 6, 1)], ["500", "0"]))

        assert s.net_cagr == -1.0

    def test_mixed_months_and_years(self) -> None:
        days = [date(2025, 11, 28), date(2025, 12, 31), date(2026, 1, 30), date(2026, 2, 27)]
        s = SwingMetrics.of(run(days, ["1100", "1000", "1100", "1210"]))

        assert s.months == 4
        assert s.positive_month_share == 0.75
        assert s.worst_month == pytest.approx(1000 / 1100 - 1)
        assert s.years == 2
        assert s.positive_year_share == 0.5  # 2025 closes flat at 1000 (not positive); 2026 rises

    def test_monthly_t_is_mean_over_its_standard_error(self) -> None:
        days = [date(2026, 1, 30), date(2026, 2, 27), date(2026, 3, 31)]
        s = SwingMetrics.of(run(days, ["1100", "1100", "1210"]))

        months = [0.10, 0.0, 0.10]
        mean = sum(months) / 3
        sd = sqrt(sum((m - mean) ** 2 for m in months) / 2)
        assert s.monthly_t == pytest.approx(mean / (sd / sqrt(3)))

    def test_concentration_is_the_largest_winners_share_of_the_total(self) -> None:
        trades = (trade("A", "300"), trade("B", "100"), trade("A", "-100"), trade("C", "-50"))
        s = SwingMetrics.of(run(list(self.DAYS), ["1100"] * 4, trades=trades))

        assert s.max_instrument_share == pytest.approx(200 / 250)  # A +200 of total +250
        assert s.round_trips == 4

    def test_concentration_is_undefined_without_a_profit(self) -> None:
        s = SwingMetrics.of(run(list(self.DAYS), ["1000"] * 4, trades=(trade("A", "-10"),)))

        assert s.max_instrument_share is None

    def test_a_run_with_no_sessions_has_no_metrics(self) -> None:
        with pytest.raises(ValueError, match="no sessions"):
            SwingMetrics.of(run([], []))

    def test_daily_returns_start_from_the_initial_capital(self) -> None:
        r = run([date(2026, 1, 2), date(2026, 1, 5)], ["1100", "990"])

        assert SwingMetrics.daily_returns(r) == pytest.approx([0.10, -0.10])
