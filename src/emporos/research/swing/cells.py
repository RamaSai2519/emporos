"""The declared Track A cells as buildable arms (EM-228, EM-229).

A cell is a declaration's grid plus the recipe that turns one grid point into a strategy. It owns no
number of its own: every parameter is read from the declared point, and `max_positions` is the
arm dimension of both cells (5 the normal posture, 3 the aggressive one: concentration, never
leverage). The recipe classes are the only place a slug meets a strategy class.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import product
from typing import Protocol

from emporos.research.swing.data import SwingDataset
from emporos.research.swing.earnings_drift import PostEarningsDrift, ReactionSessions
from emporos.research.swing.momentum import MomentumTrend
from emporos.research.swing.regime import (
    IndexTrendRegime,
    MonthlyCalendar,
    RebalanceCalendar,
    WeeklyCalendar,
)
from emporos.research.swing.rules import LossStop, SwingStrategy

__all__ = [
    "AGGRESSIVE_MAX_POSITIONS", "CELLS", "CellEnvironment", "EarningsDriftCell", "MomentumCell",
    "SwingCell", "adjacent_arms", "arm_points", "arm_label",
]  # fmt: skip

AGGRESSIVE_MAX_POSITIONS = 3

Point = Mapping[str, str]


@dataclass(frozen=True)
class CellEnvironment:
    """What a recipe may draw on: the data, the regime, the stop, and (A2) the reaction sessions."""

    dataset: SwingDataset
    regime: IndexTrendRegime
    stop: LossStop
    reactions: ReactionSessions | None = None


class SwingCell(Protocol):
    slug: str

    def strategy(self, point: Point, env: CellEnvironment) -> SwingStrategy: ...

    def effective_start(self, strategy: SwingStrategy, env: CellEnvironment) -> date | None:
        """The first session the arm could act on (warm-up over, regime defined)."""
        ...

    def arm_notes(self, strategy: SwingStrategy) -> str:
        """What the arm's own record says, for its report line."""
        ...

    def cell_notes(self, env: CellEnvironment) -> list[str]:
        """Facts about the whole cell the reader needs (A2: reaction sessions used per year)."""
        ...


def arm_points(grid: Mapping[str, Sequence[str]]) -> list[dict[str, str]]:
    """Every point of the declared grid, in declaration order (the first varies slowest)."""
    names = list(grid)
    return [dict(zip(names, values, strict=True)) for values in product(*(grid[n] for n in names))]


def arm_label(point: Point) -> str:
    return " ".join(f"{k}={v}" for k, v in point.items())


def adjacent_arms(grid: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    """label -> labels of the arms that differ in exactly one parameter, and there by one step in
    the parameter's declared value order (PROFIT_PLAN §3.2's "adjacent arms")."""
    points = arm_points(grid)
    order = {name: {v: i for i, v in enumerate(values)} for name, values in grid.items()}
    out: dict[str, list[str]] = {}
    for a in points:
        out[arm_label(a)] = [
            arm_label(b)
            for b in points
            if sum(a[n] != b[n] for n in grid) == 1
            and all(abs(order[n][a[n]] - order[n][b[n]]) <= 1 for n in grid)
        ]
    return out


class MomentumCell:
    """A1 (config/experiments/a1-momentum-trend-filter.yaml)."""

    slug = "a1-momentum-trend-filter"
    _CALENDARS: Mapping[str, Callable[[], RebalanceCalendar]] = {
        "weekly": WeeklyCalendar,
        "monthly": MonthlyCalendar,
    }

    def strategy(self, point: Point, env: CellEnvironment) -> MomentumTrend:
        return MomentumTrend(
            int(point["lookback_sessions"]),
            int(point["max_positions"]),
            self._CALENDARS[point["rebalance"]](),
            env.regime,
            env.stop,
        )

    def effective_start(self, strategy: SwingStrategy, env: CellEnvironment) -> date | None:
        return strategy.record.first_ranked_day if isinstance(strategy, MomentumTrend) else None

    def arm_notes(self, strategy: SwingStrategy) -> str:
        if not isinstance(strategy, MomentumTrend):
            return ""
        r = strategy.record
        return f"{r.rebalances} rebalances ranked, {r.stops} stops"

    def cell_notes(self, env: CellEnvironment) -> list[str]:
        return [
            "regime defined from "
            f"{env.regime.first_defined_day(env.dataset.calendar)} (NIFTY 50 > 200-session average)"
        ]


class EarningsDriftCell:
    """A2 (config/experiments/a2-post-earnings-drift.yaml)."""

    slug = "a2-post-earnings-drift"

    def strategy(self, point: Point, env: CellEnvironment) -> PostEarningsDrift:
        if env.reactions is None:
            raise ValueError("A2 needs the reaction sessions built from the results events")
        return PostEarningsDrift(
            Decimal(point["reaction_min_pct"]) / 100,
            int(point["hold_sessions"]),
            int(point["max_positions"]),
            env.reactions,
            env.regime,
            env.stop,
        )

    def effective_start(self, strategy: SwingStrategy, env: CellEnvironment) -> date | None:
        return env.regime.first_defined_day(env.dataset.calendar)

    def arm_notes(self, strategy: SwingStrategy) -> str:
        if not isinstance(strategy, PostEarningsDrift):
            return ""
        r = strategy.record
        return (
            f"{sum(r.qualified_by_year.values())} qualified, "
            f"{r.skipped_for_slots} skipped for slots, {r.stops} stops"
        )

    def cell_notes(self, env: CellEnvironment) -> list[str]:
        r = env.reactions
        if r is None:
            return []
        return [
            f"reaction sessions used per year: {r.used_by_year}",
            f"results published in-session (skipped): {r.in_session}; "
            f"reaction session missing from the bars: {r.no_session}",
        ]


CELLS: Mapping[str, SwingCell] = {c.slug: c for c in (MomentumCell(), EarningsDriftCell())}
