"""One option arm through the Track B bar (PROFIT_PLAN §3.2 and §3.4, EM-230).

`OptionBar` holds the thresholds, pinned by a test: moving one is the head's commit with the
operator's word, not a convenience of a run. `judge` names every check an arm fails; the cell-level
neighbour check (at least half of the declared adjacent arms also net positive at benchmark) is
passed in, and until it is computed an arm cannot pass. Track B's return bar is cash, not a
buy-and-hold benchmark: the arm must beat 6.5% a year."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.options.backtest import BacktestResult
from emporos.research.option_screen.stats import OptionStats
from emporos.research.swing.bootstrap import RuinReport

__all__ = ["ArmOutcome", "OptionBar", "OptionVerdict", "judge"]


@dataclass(frozen=True)
class OptionBar:
    """PROFIT_PLAN §3.2 (Track B) and §3.4, verbatim."""

    min_net_cagr: float = 0.18
    cash_rate: float = 0.065
    min_positive_month_share: float = 0.60  # of the months WITH ANY EXPOSURE (amended 2026-09-25)
    max_negative_month_share: float = 0.40  # of ALL months (amended 2026-09-25)
    min_worst_month: float = -0.10
    max_drawdown: float = 0.25
    min_monthly_t: float = 2.5
    min_round_trips: int = 100
    min_positive_year_share: float = 0.60
    min_neighbour_share: float = 0.50
    aggressive_max_p_drawdown: float = 0.05


@dataclass(frozen=True)
class ArmOutcome:
    """Everything one arm produced, on the two cost scenarios."""

    run: BacktestResult  # at BENCHMARK costs
    stats: OptionStats
    adverse_stats: OptionStats
    ruin: RuinReport
    first_day: date
    capital: Decimal
    # the same arm at zero slippage and at ADVERSE costs, for the per-spread breakdown (EM-234)
    frictionless_run: BacktestResult | None = None
    adverse_run: BacktestResult | None = None


@dataclass(frozen=True)
class OptionVerdict:
    passed: bool
    failed_checks: tuple[str, ...]


def judge(
    outcome: ArmOutcome,
    neighbours: float | None,
    *,
    aggressive: bool,
    bar: OptionBar = OptionBar(),  # noqa: B008  (a frozen value object)
) -> OptionVerdict:
    s, failed = outcome.stats, []

    def check(ok: bool, name: str) -> None:
        if not ok:
            failed.append(name)

    check(s.net_cagr >= bar.min_net_cagr, f"net CAGR >= {bar.min_net_cagr:.0%} at benchmark costs")
    check(outcome.adverse_stats.net_cagr > 0, "net CAGR > 0 at adverse costs")
    check(s.net_cagr > bar.cash_rate, f"net CAGR beats cash at {bar.cash_rate:.1%}")
    exposed = s.positive_month_share_exposed
    check(
        exposed is not None and exposed >= bar.min_positive_month_share,
        "months with exposure net positive >= 60%",
    )
    check(s.negative_month_share <= bar.max_negative_month_share, "all months net negative <= 40%")
    check(s.worst_month >= bar.min_worst_month, "worst month >= -10%")
    check(s.max_drawdown <= bar.max_drawdown, "max drawdown <= 25%")
    check(s.monthly_t is not None and s.monthly_t >= bar.min_monthly_t, "monthly t >= 2.5")
    check(s.round_trips >= bar.min_round_trips, "round trips >= 100")
    check(s.positive_year_share >= bar.min_positive_year_share, "calendar years positive >= 60%")
    check(
        neighbours is not None and neighbours >= bar.min_neighbour_share,
        "adjacent arms net positive >= 50% (cell-level; not yet computed counts as a fail)",
    )
    if aggressive:
        check(
            outcome.ruin.p_drawdown_30 <= bar.aggressive_max_p_drawdown,
            "aggressive arm: P(drawdown >= 30%) <= 5%",
        )
    return OptionVerdict(not failed, tuple(failed))
