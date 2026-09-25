"""EM-228/229: the grid, adjacency, the recipes and the cell runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest
from tests.unit.research.swing.support import ZERO_SCHEDULE, bar, dataset, series, sessions

from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.cells import (
    CELLS,
    ROTATION_CELLS,
    BookRiskMomentumCell,
    CellEnvironment,
    EarningsDriftCell,
    EtfRotationCell,
    MomentumCell,
    adjacent_arms,
    arm_label,
    arm_points,
)
from emporos.research.swing.earnings_drift import PostEarningsDrift, ReactionSessions
from emporos.research.swing.ledger import DailyPnlStore, JsonlSwingLedger
from emporos.research.swing.momentum import MomentumTrend
from emporos.research.swing.regime import (
    IndexSeries,
    IndexTrendRegime,
    MonthlyCalendar,
    WeeklyCalendar,
)
from emporos.research.swing.rules import LossStop
from emporos.research.swing.runner import SwingCellRunner
from emporos.research.swing.screen import SwingScreenRun

DAYS = sessions(90)
A1_GRID = {
    "lookback_sessions": ["126", "252"],
    "rebalance": ["weekly", "monthly"],
    "max_positions": ["5", "3"],
}


def env(reactions: ReactionSessions | None = None) -> CellEnvironment:
    up = IndexSeries(
        [bar("NSE:99926000", d, str(100 + i), str(100 + i)) for i, d in enumerate(DAYS)]
    )
    paths = {
        "NSE:1": [(str(100 + i), str(101 + i)) for i in range(90)],
        "NSE:2": [(str(200 + 2 * i), str(201 + 2 * i)) for i in range(90)],
    }
    data = dataset(*(series(n, DAYS, p) for n, p in paths.items()))
    return CellEnvironment(data, IndexTrendRegime(up, 3), LossStop(), reactions)


class TestGrid:
    def test_the_points_are_the_declared_grid_in_declaration_order(self) -> None:
        points = arm_points(A1_GRID)

        assert len(points) == 8
        assert points[0] == {
            "lookback_sessions": "126",
            "rebalance": "weekly",
            "max_positions": "5",
        }
        assert points[1]["max_positions"] == "3"  # the last parameter varies fastest
        assert points[-1] == {
            "lookback_sessions": "252",
            "rebalance": "monthly",
            "max_positions": "3",
        }

    def test_a_label_names_every_parameter(self) -> None:
        assert arm_label(arm_points(A1_GRID)[0]) == (
            "lookback_sessions=126 rebalance=weekly max_positions=5"
        )

    def test_each_arm_of_a_2x2x2_grid_has_three_neighbours(self) -> None:
        adjacent = adjacent_arms(A1_GRID)

        assert len(adjacent) == 8
        assert all(len(v) == 3 for v in adjacent.values())
        first = arm_label(arm_points(A1_GRID)[0])
        assert "lookback_sessions=252 rebalance=weekly max_positions=5" in adjacent[first]
        assert "lookback_sessions=252 rebalance=monthly max_positions=3" not in adjacent[first]

    def test_neighbours_are_one_step_apart_in_the_declared_order(self) -> None:
        grid = {"x": ["1", "2", "3"], "y": ["a", "b"]}

        adjacent = adjacent_arms(grid)

        assert set(adjacent["x=1 y=a"]) == {"x=2 y=a", "x=1 y=b"}  # not x=3
        assert set(adjacent["x=2 y=a"]) == {"x=1 y=a", "x=3 y=a", "x=2 y=b"}


class TestRecipes:
    def test_the_registry_holds_both_declared_cells(self) -> None:
        assert set(CELLS) == {
            "a1-momentum-trend-filter", "a2-post-earnings-drift", "a1b-momentum-book-risk"
        }  # fmt: skip

    def test_a1_reads_every_parameter_from_the_point(self) -> None:
        point = {"lookback_sessions": "126", "rebalance": "monthly", "max_positions": "3"}

        strategy = MomentumCell().strategy(point, env())

        assert isinstance(strategy, MomentumTrend)
        assert (strategy._lookback, strategy._n) == (126, 3)
        assert isinstance(strategy._calendar, MonthlyCalendar)

    def test_a1_weekly_uses_the_weekly_calendar(self) -> None:
        point = {"lookback_sessions": "252", "rebalance": "weekly", "max_positions": "5"}

        strategy = MomentumCell().strategy(point, env())

        assert isinstance(strategy._calendar, WeeklyCalendar)  # type: ignore[attr-defined]

    def test_a2_reads_the_reaction_threshold_as_a_percent(self) -> None:
        point = {"reaction_min_pct": "8", "hold_sessions": "40", "max_positions": "5"}
        reactions = ReactionSessions({}, {}, 0, 0)

        strategy = EarningsDriftCell().strategy(point, env(reactions))

        assert isinstance(strategy, PostEarningsDrift)
        assert strategy._reaction_min == Decimal("0.08")
        assert (strategy._hold, strategy._n) == (40, 5)

    def test_a2_without_events_is_refused(self) -> None:
        point = {"reaction_min_pct": "5", "hold_sessions": "20", "max_positions": "5"}

        with pytest.raises(ValueError, match="reaction sessions"):
            EarningsDriftCell().strategy(point, env())

    def test_a2_reports_reaction_sessions_per_year(self) -> None:
        notes = EarningsDriftCell().cell_notes(env(ReactionSessions({}, {2018: 3, 2019: 5}, 2, 1)))

        assert "{2018: 3, 2019: 5}" in notes[0]
        assert "in-session (skipped): 2" in notes[1]


class TestRunner:
    GRID: ClassVar[dict[str, list[str]]] = {
        "lookback_sessions": ["5"],
        "rebalance": ["weekly", "monthly"],
        "max_positions": ["2", "3"],
    }

    def runner(self, tmp_path: Path) -> SwingCellRunner:
        e = env()
        screen = SwingScreenRun(
            e.dataset, ZERO_SCHEDULE, SwingScreenRun.universe(e.dataset, ZERO_SCHEDULE),
            bootstrap=BlockBootstrap(paths=20),
        )  # fmt: skip
        return SwingCellRunner(
            screen, e, Decimal(100000), "test-universe", JsonlSwingLedger(tmp_path / "s.jsonl"),
            DailyPnlStore(tmp_path / "pnl"), datetime(2026, 9, 25, tzinfo=UTC),
        )  # fmt: skip

    def test_it_runs_every_arm_records_it_once_and_writes_its_daily_pnl(
        self, tmp_path: Path
    ) -> None:
        report = self.runner(tmp_path).run(MomentumCell(), self.GRID)

        assert len(report.arms) == 4
        assert all(a.counted for a in report.arms)
        assert len((tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()) == 4
        assert len(list((tmp_path / "pnl").glob("*.csv"))) == 4
        assert (report.first_day, report.last_day) == (DAYS[0], DAYS[-1])

    def test_a_book_of_three_or_fewer_names_is_the_concentrated_posture(
        self, tmp_path: Path
    ) -> None:
        grid = {**self.GRID, "max_positions": ["5", "3"]}

        report = self.runner(tmp_path).run(MomentumCell(), grid)

        assert {a.aggressive for a in report.arms if a.point["max_positions"] == "3"} == {True}
        assert {a.aggressive for a in report.arms if a.point["max_positions"] == "5"} == {False}

    def test_running_the_same_cell_again_is_not_a_new_look(self, tmp_path: Path) -> None:
        runner = self.runner(tmp_path)
        runner.run(MomentumCell(), self.GRID)

        again = runner.run(MomentumCell(), self.GRID)

        assert not any(a.counted for a in again.arms)
        assert len((tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()) == 4

    def test_each_arm_gets_its_neighbour_share_and_a_verdict(self, tmp_path: Path) -> None:
        report = self.runner(tmp_path).run(MomentumCell(), self.GRID)

        for arm in report.arms:
            assert arm.neighbour_share in (0.0, 0.5, 1.0)
            assert not arm.verdict.passed  # a handful of trades cannot clear 100 round trips
            assert any("round trips" in c for c in arm.verdict.failed_checks)

    def test_an_arm_reports_when_it_could_first_act_and_when_it_first_bought(
        self, tmp_path: Path
    ) -> None:
        report = self.runner(tmp_path).run(MomentumCell(), self.GRID)

        arm = report.arms[0]
        assert arm.effective_start is not None
        assert arm.first_entry_day is not None
        assert arm.first_entry_day > arm.effective_start
        assert "rebalances ranked" in arm.note


class TestBookRiskCell:
    def point(self, vol: str = "off", rebalance: str = "weekly") -> dict[str, str]:
        return {"rebalance": rebalance, "vol_target": vol}

    def test_it_wraps_a1_at_252_and_5_names_in_the_book_rules(self) -> None:
        from emporos.research.swing.book_risk import BookRiskRules

        strategy = BookRiskMomentumCell().strategy(self.point(), env())

        assert isinstance(strategy, BookRiskRules)
        momentum = strategy.inner
        assert isinstance(momentum, MomentumTrend)
        assert (momentum._lookback, momentum._n) == (252, 5)
        assert isinstance(momentum._calendar, WeeklyCalendar)  # type: ignore[attr-defined]

    def test_the_volatility_target_arm_wraps_the_momentum_book_in_the_target(self) -> None:
        from emporos.research.swing.book_risk import VolTargeted

        base = env()
        with_index = CellEnvironment(base.dataset, base.regime, base.stop, None, base.regime._index)

        strategy = BookRiskMomentumCell().strategy(self.point("15", "monthly"), with_index)

        targeted = strategy.inner
        assert isinstance(targeted, VolTargeted)
        assert targeted._target == Decimal("0.15")
        assert isinstance(targeted.inner, MomentumTrend)

    def test_the_target_needs_the_index_series(self) -> None:
        with pytest.raises(ValueError, match="index series"):
            BookRiskMomentumCell().strategy(self.point("15"), env())

    def test_the_book_is_five_names_and_never_the_concentrated_posture(self) -> None:
        cell = BookRiskMomentumCell()

        assert cell.max_positions(self.point()) == 5
        assert cell.aggressive(self.point()) is False

    def test_the_report_note_lists_halts_kills_and_re_entries(self) -> None:
        strategy = BookRiskMomentumCell().strategy(self.point(), env())

        note = BookRiskMomentumCell().arm_notes(strategy)

        assert "halts []" in note
        assert "kills []" in note
        assert "re-entries []" in note
        assert "rebalances ranked" in note

    def test_the_effective_start_is_the_inner_momentum_books(self) -> None:
        cell = BookRiskMomentumCell()

        assert (
            cell.effective_start(cell.strategy(self.point(), env()), env()) is None
        )  # nothing ran

    def test_the_two_by_two_grid_gives_each_arm_two_neighbours(self) -> None:
        grid = {"rebalance": ["weekly", "monthly"], "vol_target": ["off", "15"]}

        assert all(len(v) == 2 for v in adjacent_arms(grid).values())


class TestRotationCell:
    def point(self, score: str = "blend", k: str = "2") -> dict[str, str]:
        return {"score": score, "top_k": k}

    def test_it_is_registered_apart_from_the_stock_cells(self) -> None:
        assert set(ROTATION_CELLS) == {"a5-etf-dual-momentum"}
        assert "a5-etf-dual-momentum" not in CELLS

    def test_it_builds_the_rotation_over_the_datasets_assets_with_k_slots(self) -> None:
        from emporos.research.swing.rotation import EtfDualMomentum

        cell = EtfRotationCell()
        strategy = cell.strategy(self.point("12-1", "1"), env())

        assert isinstance(strategy, EtfDualMomentum)
        assert strategy._assets == ("NSE:1", "NSE:2")
        assert strategy._k == 1
        assert cell.max_positions(self.point("12-1", "1")) == 1
        assert cell.aggressive(self.point()) is False

    def test_the_stock_cells_refuse_an_environment_without_the_regime(self) -> None:
        base = env()
        bare = CellEnvironment(base.dataset, None, base.stop)

        with pytest.raises(ValueError, match="trend regime"):
            MomentumCell().strategy(
                {"lookback_sessions": "126", "rebalance": "weekly", "max_positions": "5"}, bare
            )

    def test_the_arm_note_reports_months_ranked_cash_and_holdings(self) -> None:
        cell = EtfRotationCell()

        assert "months ranked" in cell.arm_notes(cell.strategy(self.point(), env()))


def test_the_runner_starts_every_arm_on_the_given_day_and_names_the_run_by_it(
    tmp_path: Path,
) -> None:
    from datetime import date

    base = TestRunner()
    e = env()
    screen = SwingScreenRun(
        e.dataset, ZERO_SCHEDULE, SwingScreenRun.universe(e.dataset, ZERO_SCHEDULE),
        bootstrap=BlockBootstrap(paths=10),
    )  # fmt: skip
    start = e.dataset.calendar[40]
    runner = SwingCellRunner(
        screen, e, Decimal(100000), "u", JsonlSwingLedger(tmp_path / "s.jsonl"),
        DailyPnlStore(tmp_path / "pnl"), datetime(2026, 9, 25, tzinfo=UTC),
        start_day=start,
    )  # fmt: skip

    report = runner.run(MomentumCell(), base.GRID)

    assert report.first_day == start
    assert all(a.outcome.arm.days[0] == start for a in report.arms)
    first_line = json.loads((tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first_line["first_day"] == start.isoformat()
    assert isinstance(start, date)
