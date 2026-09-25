"""Run the declared A4 book: every split, neighbours, verdicts and the ledger (EM-233).

Orchestration only, the counterpart of `SwingCellRunner` for a book of sleeves. Each arm goes
through `BookScreenRun`, is judged on the §3.2 bar with the adjacent split as its neighbour, and is
written to the ledger (once) with its daily P&L. The result is the ordinary `CellReport` (so the
common report prints it) plus the book-only facts in `BookReport`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.research.gap_classes import GapVerdict
from emporos.research.swing.book_cell import EQUITY, SleeveBookCell
from emporos.research.swing.book_screen import BookArm, BookScreenRun
from emporos.research.swing.cells import CellEnvironment, adjacent_arms, arm_label, arm_points
from emporos.research.swing.exposure import real_gap_exposure
from emporos.research.swing.ledger import (
    DailyPnlStore,
    JsonlSwingLedger,
    SwingIdentity,
    SwingRecord,
)
from emporos.research.swing.runner import ArmReport, CellReport
from emporos.research.swing.screen import judge, neighbour_share

__all__ = ["BookReport", "BookRunner"]


@dataclass(frozen=True)
class BookReport:
    cell: CellReport
    arms: tuple[BookArm, ...]


class BookRunner:
    def __init__(
        self,
        screen: BookScreenRun,
        cell: SleeveBookCell,
        stock_env: CellEnvironment,
        capital: Decimal,
        universe: str,
        ledger: JsonlSwingLedger,
        pnl: DailyPnlStore,
        now: datetime,
        real_gaps: Sequence[GapVerdict],
        benchmark_label: str,
        max_positions: int,
        kind: str = "cell",
        neighbour_share_override: float | None = None,
    ) -> None:
        self._screen = screen
        self._cell = cell
        self._env = stock_env
        self._capital = capital
        self._universe = universe
        self._ledger = ledger
        self._pnl = pnl
        self._now = now
        self._real_gaps = tuple(real_gaps)
        self._benchmark_label = benchmark_label
        self._max_positions = max_positions
        self._kind = kind
        self._neighbours = neighbour_share_override

    def run(self, grid: Mapping[str, Sequence[str]]) -> BookReport:
        points = arm_points(grid)
        arms = [self._screen.run(self._cell.weights(p)) for p in points]
        positive = {
            arm_label(p): a.outcome.stats.net_profit > 0 for p, a in zip(points, arms, strict=True)
        }
        adjacent = adjacent_arms(grid)
        reports = [
            self._record(p, a, self._share(p, positive, adjacent))
            for p, a in zip(points, arms, strict=True)
        ]
        first, last = arms[0].outcome.arm.days[0], arms[0].outcome.arm.days[-1]
        return BookReport(
            CellReport(self._cell.slug, first, last, tuple(reports), (), self._benchmark_label),
            tuple(arms),
        )

    def _share(
        self,
        point: Mapping[str, str],
        positive: Mapping[str, bool],
        adjacent: Mapping[str, Sequence[str]],
    ) -> float | None:
        """A stress look has no neighbours to run: it carries the share the Discovery cell had."""
        if self._neighbours is not None:
            return self._neighbours
        return neighbour_share(arm_label(point), positive, adjacent)

    def _record(self, point: Mapping[str, str], arm: BookArm, share: float | None) -> ArmReport:
        outcome = arm.outcome
        verdict = judge(outcome, share)
        run = outcome.arm
        identity = SwingIdentity(
            self._cell.slug, point, self._universe, run.days[0], run.days[-1], self._capital,
            self._max_positions,
        )  # fmt: skip
        path = self._pnl.write(identity.screen_id, outcome)
        equity_run = next(s.run for s in arm.sleeve_runs if s.name == EQUITY)
        held = real_gap_exposure(equity_run, self._env.dataset, self._real_gaps)
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
                self._kind,
            )  # fmt: skip
        )
        note = (
            f"{self._cell.sleeve_notes(arm.strategies)}; {len(arm.book.transfers)} quarterly "
            f"resets moved Rs {sum((t.moved for t in arm.book.transfers), Decimal(0)):,.0f} "
            f"and cost Rs {arm.book.transfer_cost:,.0f}"
        )
        return ArmReport(
            point, arm_label(point), outcome, verdict, share, False,
            min((t.entry_day for t in run.trades), default=None),
            self._cell.effective_start(arm.strategies, self._env), note, held, counted,
        )  # fmt: skip
