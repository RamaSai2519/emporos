"""The Track A-core screen: an ETF core cell through the PROFIT_PLAN §10 CORE bar (EM-237).

A core is not judged as a satellite. `CoreBar` holds the §10 thresholds, pinned by a test: moving
one is the head's commit with the operator's word. On Discovery, all of these must hold:

* net CAGR at BENCHMARK costs at least the 60/40 NIFTYBEES/GOLDBEES buy-and-hold's (yearly
  rebalance, same costs, same fill rule, same days), and not more than 2 points below it in either
  half of the window (the halves split at 2016-12-31);
* max drawdown at most 0.6 of that benchmark's; worst month at least -10%; the amended §3.2 months
  bar (>= 60% of the months with exposure positive AND <= 40% of ALL months negative); monthly
  t >= 2.5; §3.4 P(drawdown >= 30%) <= 5%; net CAGR > 0 at ADVERSE costs.
* No round-trip count and no Sharpe comparison: a monthly allocation trades rarely, and the
  monthly t carries the statistical burden.

`CoreMeasures` gathers every number the bar reads (and the report shows) once, from the arm's
outcome and the benchmark's run; `judge_core` reads only that.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from emporos.domain.fees import FeeSchedule
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.costs import ADVERSE, BENCHMARK, CostScenario, SwingCostModel
from emporos.research.swing.data import SwingDataset
from emporos.research.swing.metrics import SwingMetrics, SwingStats
from emporos.research.swing.regime import MonthlyCalendar
from emporos.research.swing.rules import SwingStrategy
from emporos.research.swing.screen import ArmOutcome, SwingVerdict
from emporos.research.swing.simulator import FillPrice, SwingRun
from emporos.research.swing.weighted import TargetWeightBook, TargetWeights

__all__ = ["CoreBar", "CoreMeasures", "CorePolicy", "CoreScreenRun", "HALF_SPLIT", "judge_core"]

HALF_SPLIT = date(2016, 12, 31)  # the first half ends here; the second starts the next day


class CorePolicy(TargetWeights, SwingStrategy, Protocol):
    """What a core arm is: target weights for the book, and (in name only) the arm's `strategy`
    that its outcome carries with its own record."""


@dataclass(frozen=True)
class CoreBar:
    """PROFIT_PLAN §10 verbatim."""

    max_half_shortfall: float = 0.02  # CAGR points below the benchmark allowed in either half
    max_drawdown_ratio: float = 0.6
    min_worst_month: float = -0.10
    min_exposed_positive_month_share: float = 0.60
    max_negative_month_share: float = 0.40
    min_monthly_t: float = 2.5
    max_p_drawdown_30: float = 0.05


@dataclass(frozen=True)
class CoreMeasures:
    stats: SwingStats  # the arm, whole window
    benchmark: SwingStats
    first_half: SwingStats  # the arm, to the split
    second_half: SwingStats
    benchmark_first_half: SwingStats
    benchmark_second_half: SwingStats
    adverse_cagr: float
    p_drawdown_30: float

    @staticmethod
    def of(outcome: ArmOutcome, benchmark: SwingRun, split: date = HALF_SPLIT) -> CoreMeasures:
        second = split + timedelta(days=1)
        return CoreMeasures(
            outcome.stats,
            SwingMetrics.of(benchmark),
            SwingMetrics.of(outcome.arm.slice_until(split)),
            SwingMetrics.of(outcome.arm.slice_from(second)),
            SwingMetrics.of(benchmark.slice_until(split)),
            SwingMetrics.of(benchmark.slice_from(second)),
            outcome.adverse_stats.net_cagr,
            outcome.ruin.p_drawdown_30,
        )


def judge_core(measures: CoreMeasures, bar: CoreBar = CoreBar()) -> SwingVerdict:  # noqa: B008
    m, s, failed = measures, measures.stats, []

    def check(ok: bool, name: str) -> None:
        if not ok:
            failed.append(name)

    check(s.net_cagr >= m.benchmark.net_cagr, "net CAGR >= the 60/40 benchmark's")
    slack = bar.max_half_shortfall
    check(
        m.first_half.net_cagr >= m.benchmark_first_half.net_cagr - slack,
        "first-half net CAGR not more than 2 points below the benchmark's",
    )
    check(
        m.second_half.net_cagr >= m.benchmark_second_half.net_cagr - slack,
        "second-half net CAGR not more than 2 points below the benchmark's",
    )
    check(
        s.max_drawdown <= bar.max_drawdown_ratio * m.benchmark.max_drawdown,
        "max drawdown <= 0.6 x the benchmark's",
    )
    check(s.worst_month >= bar.min_worst_month, "worst month >= -10%")
    exposed = s.positive_month_share_exposed
    check(
        exposed is not None and exposed >= bar.min_exposed_positive_month_share,
        "months with exposure net positive >= 60%",
    )
    check(s.negative_month_share <= bar.max_negative_month_share, "all months net negative <= 40%")
    check(s.monthly_t is not None and s.monthly_t >= bar.min_monthly_t, "monthly t >= 2.5")
    check(m.p_drawdown_30 <= bar.max_p_drawdown_30, "P(drawdown >= 30%) <= 5%")
    check(m.adverse_cagr > 0, "net CAGR > 0 at adverse costs")
    return SwingVerdict(not failed, tuple(failed))


class CoreScreenRun:
    """One core arm: the book at BENCHMARK and at ADVERSE costs, against the benchmark's run
    (built once per cell by the caller, same fill rule and days), and the §3.4 bootstrap."""

    def __init__(
        self,
        dataset: SwingDataset,
        schedule: FeeSchedule,
        assets: Sequence[str],
        benchmark: SwingRun,
        capital: Decimal,
        cash_yield: Decimal,
        start_day: date,
        bootstrap: BlockBootstrap | None = None,
    ) -> None:
        self._dataset = dataset
        self._schedule = schedule
        self._assets = tuple(assets)
        self._benchmark_stats = SwingMetrics.of(benchmark)
        self._capital = capital
        self._cash_yield = cash_yield
        self._start_day = start_day
        self._bootstrap = bootstrap or BlockBootstrap()

    def run(self, policy: Callable[[], CorePolicy]) -> ArmOutcome:
        """`policy` builds a fresh instance for each run; the benchmark-cost one (in the outcome,
        with its record) is the arm's own."""
        first = policy()
        arm = self._simulate(first, BENCHMARK)
        adverse = self._simulate(policy(), ADVERSE)
        return ArmOutcome(
            arm,
            first,
            SwingMetrics.of(arm),
            SwingMetrics.of(adverse),
            self._benchmark_stats,
            self._bootstrap.report(SwingMetrics.daily_returns(arm)),
        )

    def _simulate(self, policy: TargetWeights, scenario: CostScenario) -> SwingRun:
        return TargetWeightBook(
            self._dataset, policy, self._assets, SwingCostModel(self._schedule, scenario),
            MonthlyCalendar(), self._capital, self._cash_yield, self._start_day, FillPrice.CLOSE,
            decide_at_start=False,
        ).run()  # fmt: skip
