"""EM-237: the PROFIT_PLAN §10 core bar, pinned, and its judge check by check."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from tests.unit.research.swing.support import ZERO_SCHEDULE, dataset, free_costs, series, sessions

from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.core_screen import (
    HALF_SPLIT,
    CoreBar,
    CoreMeasures,
    CoreScreenRun,
    judge_core,
)
from emporos.research.swing.metrics import SwingStats
from emporos.research.swing.regime import YearlyCalendar
from emporos.research.swing.simulator import SwingRun
from emporos.research.swing.trend_core import TrendCorePolicy
from emporos.research.swing.weighted import WeightedBuyHold


def stats(**over: object) -> SwingStats:
    base = SwingStats(
        net_cagr=0.12, net_sharpe=1.2, total_return=1.0, months=120, positive_month_share=0.7,
        worst_month=-0.05, monthly_t=3.0, years=10, positive_year_share=0.8, max_drawdown=0.10,
        round_trips=40, days=2500, days_in_cash=300, max_instrument_share=None, net_profit=1e5,
        months_with_exposure=110, positive_month_share_exposed=0.7, negative_month_share=0.25,
    )  # fmt: skip
    return replace(base, **over)  # type: ignore[arg-type]


def measures(**over: object) -> CoreMeasures:
    bench = stats(net_cagr=0.10, max_drawdown=0.20)
    base = CoreMeasures(
        stats=stats(), benchmark=bench, first_half=stats(net_cagr=0.10),
        second_half=stats(net_cagr=0.14), benchmark_first_half=bench,
        benchmark_second_half=stats(net_cagr=0.12), adverse_cagr=0.10, p_drawdown_30=0.0,
    )  # fmt: skip
    return replace(base, **over)  # type: ignore[arg-type]


class TestTheBarIsPlan10:
    def test_the_thresholds_are_the_plan_s(self) -> None:
        bar = CoreBar()

        assert bar.max_half_shortfall == 0.02
        assert bar.max_drawdown_ratio == 0.6
        assert bar.min_worst_month == -0.10
        assert bar.min_exposed_positive_month_share == 0.60
        assert bar.max_negative_month_share == 0.40
        assert bar.min_monthly_t == 2.5
        assert bar.max_p_drawdown_30 == 0.05

    def test_the_halves_split_at_the_end_of_2016(self) -> None:
        assert date(2016, 12, 31) == HALF_SPLIT


class TestJudge:
    def test_a_sound_core_passes_with_no_round_trip_or_sharpe_requirement(self) -> None:
        m = measures(stats=stats(round_trips=3, net_sharpe=0.1))

        verdict = judge_core(m)

        assert verdict.passed, verdict.failed_checks

    def _failed(self, **over: object) -> tuple[str, ...]:
        return judge_core(measures(**over)).failed_checks

    def test_cagr_below_the_benchmark_fails(self) -> None:
        assert self._failed(stats=stats(net_cagr=0.09)) == ("net CAGR >= the 60/40 benchmark's",)

    def test_a_half_more_than_two_points_below_fails_and_exactly_two_passes(self) -> None:
        assert self._failed(first_half=stats(net_cagr=0.0799)) == (
            "first-half net CAGR not more than 2 points below the benchmark's",
        )
        assert self._failed(first_half=stats(net_cagr=0.0801)) == ()
        assert self._failed(second_half=stats(net_cagr=0.0999)) == (
            "second-half net CAGR not more than 2 points below the benchmark's",
        )

    def test_the_drawdown_limit_is_six_tenths_of_the_benchmarks(self) -> None:
        assert self._failed(stats=stats(max_drawdown=0.121)) == (
            "max drawdown <= 0.6 x the benchmark's",
        )
        assert self._failed(stats=stats(max_drawdown=0.119)) == ()

    def test_worst_month_below_minus_ten_percent_fails(self) -> None:
        assert self._failed(stats=stats(worst_month=-0.101)) == ("worst month >= -10%",)

    def test_the_two_months_checks_are_the_amended_ones(self) -> None:
        low = self._failed(stats=stats(positive_month_share_exposed=0.59))
        high = self._failed(stats=stats(negative_month_share=0.41))

        assert low == ("months with exposure net positive >= 60%",)
        assert high == ("all months net negative <= 40%",)

    def test_monthly_t_below_two_and_a_half_fails(self) -> None:
        assert self._failed(stats=stats(monthly_t=2.4)) == ("monthly t >= 2.5",)
        assert self._failed(stats=stats(monthly_t=None)) == ("monthly t >= 2.5",)

    def test_the_bootstrap_gate_and_the_adverse_cagr_apply(self) -> None:
        assert self._failed(p_drawdown_30=0.051) == ("P(drawdown >= 30%) <= 5%",)
        assert self._failed(adverse_cagr=0.0) == ("net CAGR > 0 at adverse costs",)


DAYS = sessions(700, date(2015, 1, 5))
A, B = "NSE:1", "NSE:2"


def rising(rate: str) -> list[tuple[str, str]]:
    price, out = Decimal(100), []
    for _ in DAYS:
        nxt = price * (1 + Decimal(rate))
        out.append((str(price), str(nxt)))
        price = nxt
    return out


class TestTheRun:
    def test_an_arm_is_run_at_two_costs_and_measured_against_the_benchmark_in_halves(self) -> None:
        data = dataset(series(A, DAYS, rising("0.0008")), series(B, DAYS, rising("0.0004")))
        start = DAYS[300]
        from emporos.research.swing.simulator import FillPrice

        bench: SwingRun = WeightedBuyHold(
            data, {A: Decimal("0.6"), B: Decimal("0.4")}, free_costs(), YearlyCalendar(),
            Decimal(100000), Decimal(0), start, FillPrice.CLOSE,
        ).run()  # fmt: skip
        screen = CoreScreenRun(
            data, ZERO_SCHEDULE, [A, B], bench, Decimal(100000), Decimal(0), start,
            BlockBootstrap(paths=20),
        )  # fmt: skip

        outcome = screen.run(lambda: TrendCorePolicy("all_equal", [A, B], None))
        m = CoreMeasures.of(outcome, bench)

        assert outcome.arm.days[0] == start
        assert isinstance(outcome.strategy, TrendCorePolicy)
        assert outcome.strategy.record.decisions > 0
        assert m.first_half.days + m.second_half.days == m.stats.days
        assert m.benchmark_first_half.days == m.first_half.days
        assert m.stats.net_cagr > 0
