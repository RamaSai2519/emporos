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

from emporos.research.swing.cells import (
    AGGRESSIVE_MAX_POSITIONS,
    CellEnvironment,
    SwingCell,
    adjacent_arms,
    arm_label,
    arm_points,
)
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

__all__ = ["ArmReport", "CellReport", "SwingCellRunner"]


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
    counted: bool  # False when the identical arm was already in the ledger


@dataclass(frozen=True)
class CellReport:
    slug: str
    first_day: date
    last_day: date
    arms: tuple[ArmReport, ...]
    notes: tuple[str, ...]


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
    ) -> None:
        self._screen = screen
        self._env = env
        self._capital = capital
        self._universe = universe
        self._ledger = ledger
        self._pnl = pnl
        self._now = now

    def run(self, cell: SwingCell, grid: Mapping[str, Sequence[str]]) -> CellReport:
        calendar = self._env.dataset.calendar
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
            aggressive = int(point["max_positions"]) <= AGGRESSIVE_MAX_POSITIONS
            verdict = judge(outcome, share, aggressive=aggressive)
            identity = SwingIdentity(
                cell.slug, point, self._universe, calendar[0], calendar[-1], self._capital,
                int(point["max_positions"]),
            )  # fmt: skip
            path = self._pnl.write(identity.screen_id, outcome)
            counted = self._ledger.record(
                SwingRecord(identity, outcome, verdict, share, self._now, path)
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
                    counted,
                )  # fmt: skip
            )
        return CellReport(
            cell.slug, calendar[0], calendar[-1], tuple(reports), tuple(cell.cell_notes(self._env))
        )

    def _run_arm(self, cell: SwingCell, point: Mapping[str, str]) -> ArmOutcome:
        config = SwingConfig(self._capital, int(point["max_positions"]))
        return self._screen.run(lambda: cell.strategy(point, self._env), config)
