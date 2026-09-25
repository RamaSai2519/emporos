"""Run a declared Track A cell: every arm, neighbours, verdicts and the ledger (EM-228, EM-229).

`SwingCellRunner` is orchestration only. It takes the declared grid and a `SwingCell` recipe, runs
every arm through `SwingScreenRun` (arm at benchmark and adverse costs, same-universe buy-and-hold,
block bootstrap), asks the cell-level questions (does at least half of an arm's adjacent arms net
positive at benchmark costs?), judges each arm against the §3.2 bar (the §3.4 gate on for the
concentrated N = 3 arms), and writes each arm to the ledger (once) with its daily P&L.

An arm is `aggressive` when its book is the concentrated one (`AGGRESSIVE_MAX_POSITIONS` names).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from emporos.research.gap_classes import GapVerdict
from emporos.research.swing.cells import (
    CellEnvironment,
    SwingCell,
    adjacent_arms,
    arm_label,
    arm_points,
)
from emporos.research.swing.exposure import RealGapExposure, real_gap_exposure
from emporos.research.swing.ledger import (
    DailyPnlStore,
    JsonlSwingLedger,
    SwingIdentity,
    SwingRecord,
)
from emporos.research.swing.screen import (
    ArmOutcome,
    SwingScreenRun,
    SwingVerdict,
    judge,
    neighbour_share,
)
from emporos.research.swing.simulator import SwingConfig

__all__ = ["BENCHMARK_LABEL", "ArmReport", "CellReport", "SwingCellRunner"]

BENCHMARK_LABEL = "same-universe equal-weight buy-and-hold, same costs"


@dataclass(frozen=True)
class ArmReport:
    point: Mapping[str, str]
    label: str
    outcome: ArmOutcome
    verdict: SwingVerdict
    neighbour_share: float | None
    aggressive: bool
    first_entry_day: date | None
    effective_start: date | None  # the first session the arm could act on
    note: str  # the strategy's own record
    real_gaps_held: tuple[RealGapExposure, ...]  # every real gap a held position went through
    counted: bool  # False when the identical arm was already in the ledger


@dataclass(frozen=True)
class CellReport:
    slug: str
    first_day: date
    last_day: date
    arms: tuple[ArmReport, ...]
    notes: tuple[str, ...]
    benchmark_label: str


class SwingCellRunner:
    def __init__(
        self,
        screen: SwingScreenRun,
        env: CellEnvironment,
        capital: Decimal,
        universe: str,
        ledger: JsonlSwingLedger,
        pnl: DailyPnlStore,
        now: datetime,
        real_gaps: Sequence[GapVerdict] = (),
        benchmark_label: str = BENCHMARK_LABEL,
        cash_yield: Decimal = Decimal(0),
        start_day: date | None = None,
    ) -> None:
        self._screen = screen
        self._env = env
        self._capital = capital
        self._universe = universe
        self._ledger = ledger
        self._pnl = pnl
        self._now = now
        self._real_gaps = tuple(real_gaps)
        self._benchmark_label = benchmark_label
        self._cash_yield = cash_yield
        self._start_day = start_day

    def run(self, cell: SwingCell, grid: Mapping[str, Sequence[str]]) -> CellReport:
        points = arm_points(grid)
        outcomes = [self._run_arm(cell, point) for point in points]
        positive = {
            arm_label(p): o.stats.net_profit > 0 for p, o in zip(points, outcomes, strict=True)
        }
        adjacent = adjacent_arms(grid)
        reports: list[ArmReport] = []
        for point, outcome in zip(points, outcomes, strict=True):
            label = arm_label(point)
            share = neighbour_share(label, positive, adjacent)
            aggressive = cell.aggressive(point)
            verdict = judge(outcome, share, aggressive=aggressive)
            first_day, last_day = outcome.arm.days[0], outcome.arm.days[-1]
            identity = SwingIdentity(
                cell.slug, point, self._universe, first_day, last_day, self._capital,
                cell.max_positions(point),
            )  # fmt: skip
            path = self._pnl.write(identity.screen_id, outcome)
            held = real_gap_exposure(outcome.arm, self._env.dataset, self._real_gaps)
            counted = self._ledger.record(
                SwingRecord(
                    identity,
                    outcome,
                    verdict,
                    share,
                    self._now,
                    path,
                    len(held),
                    sum((e.pnl for e in held), Decimal(0)),
                )  # fmt: skip
            )
            trades = outcome.arm.trades
            first_entry = min((t.entry_day for t in trades), default=None)
            reports.append(
                ArmReport(
                    point,
                    label,
                    outcome,
                    verdict,
                    share,
                    aggressive,
                    first_entry,
                    cell.effective_start(outcome.strategy, self._env),
                    cell.arm_notes(outcome.strategy),
                    held,
                    counted,
                )  # fmt: skip
            )
        return CellReport(
            cell.slug,
            reports[0].outcome.arm.days[0],
            reports[0].outcome.arm.days[-1],
            tuple(reports),
            tuple(cell.cell_notes(self._env)),
            self._benchmark_label,
        )

    def _run_arm(self, cell: SwingCell, point: Mapping[str, str]) -> ArmOutcome:
        config = SwingConfig(
            self._capital,
            cell.max_positions(point),
            cash_yield=self._cash_yield,
            start_day=self._start_day,
        )
        return self._screen.run(lambda: cell.strategy(point, self._env), config)
