"""EM-223: an arm through the §3.2 bar, the neighbour check, and the pinned thresholds."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import ClassVar

import pytest
from tests.unit.research.swing.support import ZERO_SCHEDULE, dataset, hold, series, sessions

from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.metrics import SwingStats
from emporos.research.swing.screen import (
    ArmOutcome,
    SwingBar,
    SwingScreenRun,
    judge,
    neighbour_share,
)
from emporos.research.swing.simulator import SwingConfig

X = "NSE:1"
DAYS = sessions(60)


def rising(daily: str = "0.5") -> list[tuple[str, str]]:
    price, out = Decimal(100), []
    for _ in DAYS:
        nxt = price * (1 + Decimal(daily) / 100)
        out.append((str(price), str(nxt)))
        price = nxt
    return out


def stats(**over: object) -> SwingStats:
    base = SwingStats(
        net_cagr=0.30, net_sharpe=1.5, total_return=0.3, months=60, positive_month_share=0.7,
        worst_month=-0.05, monthly_t=3.0, years=5, positive_year_share=0.8, max_drawdown=0.15,
        round_trips=150, days=1250, days_in_cash=300, max_instrument_share=0.15, net_profit=30000.0,
        months_with_exposure=50, positive_month_share_exposed=0.7, negative_month_share=0.3,
    )  # fmt: skip
    return replace(base, **over)  # type: ignore[arg-type]


def outcome(arm: SwingStats, adverse: SwingStats | None = None, universe: SwingStats | None = None,
            p_dd: float = 0.01) -> ArmOutcome:  # fmt: skip
    from emporos.research.swing.bootstrap import RuinReport

    real = SwingScreenRun(
        dataset(series(X, DAYS, rising())),
        ZERO_SCHEDULE,
        SwingScreenRun.universe(dataset(series(X, DAYS, rising())), ZERO_SCHEDULE),
        bootstrap=BlockBootstrap(paths=10),
    ).run(lambda: hold(X, DAYS[0], DAYS[-2]), SwingConfig(Decimal(10000), 1))
    return replace(
        real,
        stats=arm,
        adverse_stats=adverse or stats(net_cagr=0.10),
        universe_stats=universe or stats(net_sharpe=1.0),
        ruin=RuinReport(10, 20, 252, 1, p_dd, 0.1, 0.2),
    )


class TestBar:
    def test_the_thresholds_are_the_plans_and_pinned(self) -> None:
        bar = SwingBar()

        assert (bar.min_net_cagr, bar.min_worst_month) == (0.18, -0.10)
        assert (bar.min_exposed_positive_month_share, bar.max_negative_month_share) == (0.60, 0.40)
        assert (bar.max_drawdown, bar.min_monthly_t, bar.min_round_trips) == (0.25, 2.5, 100)
        assert (bar.min_positive_year_share, bar.max_instrument_share) == (0.60, 0.25)
        assert (bar.min_neighbour_share, bar.aggressive_max_p_drawdown) == (0.50, 0.05)


class TestJudge:
    def test_an_arm_that_clears_everything_passes(self) -> None:
        verdict = judge(outcome(stats()), neighbours=0.75)

        assert verdict.passed
        assert verdict.failed_checks == ()

    @pytest.mark.parametrize(
        ("field", "value", "check"),
        [
            ("net_cagr", 0.17, "net CAGR >= 18%"),
            ("positive_month_share_exposed", 0.59, "months with exposure net positive"),
            ("positive_month_share_exposed", None, "months with exposure net positive"),
            ("negative_month_share", 0.41, "all months net negative"),
            ("worst_month", -0.11, "worst month"),
            ("max_drawdown", 0.26, "max drawdown"),
            ("monthly_t", 2.4, "monthly t"),
            ("monthly_t", None, "monthly t"),
            ("round_trips", 99, "round trips"),
            ("positive_year_share", 0.59, "calendar years"),
            ("max_instrument_share", 0.26, "no single stock"),
            ("max_instrument_share", None, "no single stock"),
        ],
    )
    def test_each_bar_is_a_hard_line(self, field: str, value: object, check: str) -> None:
        verdict = judge(outcome(stats(**{field: value})), neighbours=0.75)

        assert not verdict.passed
        assert any(check in failed for failed in verdict.failed_checks), verdict.failed_checks

    def test_a_bar_met_exactly_passes(self) -> None:
        exact = stats(
            net_cagr=0.18,
            positive_month_share_exposed=0.60,
            negative_month_share=0.40,
            worst_month=-0.10,
            max_drawdown=0.25,
            monthly_t=2.5,
            round_trips=100,
            positive_year_share=0.60,
            max_instrument_share=0.25,
        )

        assert judge(outcome(exact), neighbours=0.50).passed

    def test_it_must_be_positive_at_adverse_costs(self) -> None:
        verdict = judge(outcome(stats(), adverse=stats(net_cagr=0.0)), neighbours=1.0)

        assert verdict.failed_checks == ("net CAGR > 0 at adverse costs",)

    def test_it_must_beat_the_same_universe_buy_and_hold_on_sharpe(self) -> None:
        tie = judge(outcome(stats(net_sharpe=1.0), universe=stats(net_sharpe=1.0)), neighbours=1.0)
        no_sharpe = judge(outcome(stats(net_sharpe=None)), neighbours=1.0)

        assert not tie.passed
        assert not no_sharpe.passed
        assert "same-universe" in tie.failed_checks[0]

    def test_the_neighbour_check_fails_below_half_and_when_not_computed(self) -> None:
        assert judge(outcome(stats()), neighbours=0.49).failed_checks
        assert judge(outcome(stats()), neighbours=None).failed_checks

    def test_an_aggressive_arm_also_needs_a_small_chance_of_a_30_percent_drawdown(self) -> None:
        risky = outcome(stats(), p_dd=0.06)

        assert judge(risky, neighbours=1.0).passed  # not flagged aggressive: no ruin gate
        verdict = judge(risky, neighbours=1.0, aggressive=True)
        assert verdict.failed_checks == ("aggressive arm: P(drawdown >= 30%) <= 5%",)
        assert judge(outcome(stats(), p_dd=0.05), neighbours=1.0, aggressive=True).passed


class TestNeighbours:
    ADJ: ClassVar[dict[str, list[str]]] = {"a": ["b", "c"], "b": ["a"], "c": ["a"], "solo": []}

    def test_it_is_the_share_of_adjacent_arms_net_positive(self) -> None:
        positive = {"a": True, "b": True, "c": False, "solo": True}

        assert neighbour_share("a", positive, self.ADJ) == 0.5
        assert neighbour_share("b", positive, self.ADJ) == 1.0
        assert neighbour_share("c", positive, self.ADJ) == 1.0

    def test_an_arm_with_no_neighbours_has_none(self) -> None:
        assert neighbour_share("solo", {"solo": True}, self.ADJ) is None
        assert neighbour_share("unknown", {}, self.ADJ) is None


class TestRun:
    def test_it_runs_the_arm_at_both_costs_and_carries_the_universe_and_the_pnl(self) -> None:
        data = dataset(series(X, DAYS, rising()))
        universe = SwingScreenRun.universe(data, ZERO_SCHEDULE)
        screen = SwingScreenRun(data, ZERO_SCHEDULE, universe, bootstrap=BlockBootstrap(paths=20))

        result = screen.run(lambda: hold(X, DAYS[0], DAYS[-2]), SwingConfig(Decimal(10000), 1))

        assert result.stats.net_cagr > 0
        assert result.adverse_stats.net_cagr > 0
        assert result.universe_stats.days == len(DAYS)
        assert len(result.daily_pnl) == len(DAYS)
        assert result.daily_pnl[0] == (date(2026, 1, 5), Decimal(0))
        assert sum(pnl for _, pnl in result.daily_pnl) == result.arm.equity[-1] - result.arm.capital
        assert result.stats.days_in_cash == result.arm.days_in_cash
        assert result.ruin.paths == 20
