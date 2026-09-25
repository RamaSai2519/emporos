"""The Track A screen: one arm through the §3.2 bar (PROFIT_PLAN §3, EM-223).

`SwingBar` holds the thresholds. They are PROFIT_PLAN §3.2, pinned by a test: moving one is the
head's commit with the operator's word, not a convenience of a run.

`SwingScreenRun` runs one arm three ways and judges it: the arm at BENCHMARK costs, the arm at
ADVERSE costs, and (once per cell, passed in) the same-universe equal-weight buy-and-hold at
BENCHMARK costs. It also block-bootstraps the arm's daily returns (§3.4). The outcome carries the
arm's daily P&L series, so a later step can combine arms by correlation, and its days in cash.

The neighbour check (at least half of the declared adjacent arms also net positive at benchmark
costs) is a property of the CELL, so it is computed by `neighbour_share` from the whole grid and
handed to `judge`. Until it is, an arm's verdict says so and cannot pass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.domain.fees import FeeSchedule
from emporos.research.swing.benchmark import EqualWeightBenchmark
from emporos.research.swing.bootstrap import BlockBootstrap, RuinReport
from emporos.research.swing.costs import ADVERSE, BENCHMARK, CostScenario, SwingCostModel
from emporos.research.swing.data import SwingDataset
from emporos.research.swing.metrics import SwingMetrics, SwingStats
from emporos.research.swing.rules import Membership, SwingStrategy
from emporos.research.swing.simulator import SwingConfig, SwingRun, SwingSimulator

__all__ = ["ArmOutcome", "SwingBar", "SwingScreenRun", "SwingVerdict", "judge", "neighbour_share"]


@dataclass(frozen=True)
class SwingBar:
    """PROFIT_PLAN §3.2 (as amended at e4f5b24) and §3.4, verbatim. The months bar is two checks:
    at least 60% of the months with any exposure net positive, and at most 40% of ALL months net
    negative: an all-cash month is neither a win nor a loss, and a book cannot pass by sitting
    out."""

    min_net_cagr: float = 0.18
    min_exposed_positive_month_share: float = 0.60
    max_negative_month_share: float = 0.40
    min_worst_month: float = -0.10
    max_drawdown: float = 0.25
    min_monthly_t: float = 2.5
    min_round_trips: int = 100
    min_positive_year_share: float = 0.60
    max_instrument_share: float = 0.25
    min_neighbour_share: float = 0.50
    aggressive_max_p_drawdown: float = 0.05


@dataclass(frozen=True)
class ArmOutcome:
    """Everything one arm produced, on the three runs."""

    arm: SwingRun  # at BENCHMARK costs
    strategy: SwingStrategy  # the instance that produced `arm`: its record is the arm's own
    stats: SwingStats
    adverse_stats: SwingStats
    universe_stats: SwingStats  # the equal-weight buy-and-hold, same costs
    ruin: RuinReport

    @property
    def daily_pnl(self) -> tuple[tuple[date, Decimal], ...]:
        return tuple(zip(self.arm.days, self.arm.daily_pnl, strict=True))


@dataclass(frozen=True)
class SwingVerdict:
    passed: bool
    failed_checks: tuple[str, ...]


class SwingScreenRun:
    def __init__(
        self,
        dataset: SwingDataset,
        schedule: FeeSchedule,
        universe_run: SwingRun,
        membership: Membership | None = None,
        bootstrap: BlockBootstrap | None = None,
    ) -> None:
        """`universe_run` is the equal-weight buy-and-hold at BENCHMARK costs over the same data
        (build it once per cell with `SwingScreenRun.universe`): an arm cannot pick its own."""
        self._dataset = dataset
        self._schedule = schedule
        self._universe_stats = SwingMetrics.of(universe_run)
        self._membership = membership
        self._bootstrap = bootstrap or BlockBootstrap()

    @staticmethod
    def universe(
        dataset: SwingDataset, schedule: FeeSchedule, membership: Membership | None = None
    ) -> SwingRun:
        costs = SwingCostModel(schedule, BENCHMARK)
        return EqualWeightBenchmark(dataset, costs, membership).run()

    def run(self, strategy: Callable[[], SwingStrategy], config: SwingConfig) -> ArmOutcome:
        """`strategy` builds a fresh instance for each run, so no state (or record) carries from
        the benchmark-cost run into the adverse-cost run."""
        first = strategy()
        arm = self._simulate(first, config, BENCHMARK)
        adverse = self._simulate(strategy(), config, ADVERSE)
        return ArmOutcome(
            arm,
            first,
            SwingMetrics.of(arm),
            SwingMetrics.of(adverse),
            self._universe_stats,
            self._bootstrap.report(SwingMetrics.daily_returns(arm)),
        )

    def _simulate(
        self, strategy: SwingStrategy, config: SwingConfig, scenario: CostScenario
    ) -> SwingRun:
        costs = SwingCostModel(self._schedule, scenario)
        return SwingSimulator(self._dataset, strategy, config, costs, self._membership).run()


def neighbour_share(
    arm: str, positive: Mapping[str, bool], adjacent: Mapping[str, Sequence[str]]
) -> float | None:
    """The share of `arm`'s declared neighbours whose net is positive at benchmark costs, or None
    when it has none (a one-arm cell has no neighbours to agree with it)."""
    neighbours = adjacent.get(arm, ())
    if not neighbours:
        return None
    return sum(positive[n] for n in neighbours) / len(neighbours)


def judge(
    outcome: ArmOutcome,
    neighbours: float | None,
    *,
    aggressive: bool = False,
    bar: SwingBar = SwingBar(),  # noqa: B008  (a frozen value object)
) -> SwingVerdict:
    s, failed = outcome.stats, []

    def check(ok: bool, name: str) -> None:
        if not ok:
            failed.append(name)

    check(s.net_cagr >= bar.min_net_cagr, f"net CAGR >= {bar.min_net_cagr:.0%} at benchmark costs")
    check(outcome.adverse_stats.net_cagr > 0, "net CAGR > 0 at adverse costs")
    sharpe, universe = s.net_sharpe, outcome.universe_stats.net_sharpe
    check(
        sharpe is not None and universe is not None and sharpe > universe,
        "net Sharpe beats the same-universe equal-weight buy-and-hold",
    )
    exposed = s.positive_month_share_exposed
    check(
        exposed is not None and exposed >= bar.min_exposed_positive_month_share,
        "months with exposure net positive >= 60%",
    )
    check(s.negative_month_share <= bar.max_negative_month_share, "all months net negative <= 40%")
    check(s.worst_month >= bar.min_worst_month, "worst month >= -10%")
    check(s.max_drawdown <= bar.max_drawdown, "max drawdown <= 25%")
    check(s.monthly_t is not None and s.monthly_t >= bar.min_monthly_t, "monthly t >= 2.5")
    check(s.round_trips >= bar.min_round_trips, "round trips >= 100")
    check(s.positive_year_share >= bar.min_positive_year_share, "calendar years positive >= 60%")
    check(
        s.max_instrument_share is not None and s.max_instrument_share <= bar.max_instrument_share,
        "no single stock > 25% of net profit (a broad index ETF is exempt)",
    )
    check(
        neighbours is not None and neighbours >= bar.min_neighbour_share,
        "adjacent arms net positive >= 50% (cell-level; not yet computed counts as a fail)",
    )
    if aggressive:
        check(
            outcome.ruin.p_drawdown_30 <= bar.aggressive_max_p_drawdown,
            "aggressive arm: P(drawdown >= 30%) <= 5%",
        )
    return SwingVerdict(not failed, tuple(failed))
