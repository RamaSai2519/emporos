"""The S1 arm table: killed date, skipped signals and the trial-log line."""

from __future__ import annotations

from datetime import date

from emporos.research.s1.arms import Arm
from emporos.research.s1.metrics import ArmMetrics, Bars
from emporos.research.s1.report import TRIAL_LOG, arm_table
from emporos.research.s1.runner import ArmOutcome


def outcome(killed: date | None) -> ArmOutcome:
    metrics = ArmMetrics(120, -50.0, -900.0, 0.4, -1.2, 3_000.0, 0.25, 12, killed)
    return ArmOutcome(
        Arm("cash", "half", 5, 0.005, "atr"), metrics, 0.5, -800.0, {"2024-01": 10},
        {"open_position": 7, "killed": 3}, 500,
    )  # fmt: skip


def test_the_table_shows_the_kill_date_the_control_p_and_the_skipped_count() -> None:
    header, killed, alive = (arm_table([outcome(date(2024, 2, 5)), outcome(None)], Bars()))[0:1] + (
        arm_table([outcome(date(2024, 2, 5)), outcome(None)], Bars())[1:]
    )

    assert "killed on" in header and "skipped" in header
    assert "2024-02-05" in killed and "0.500" in killed and " 10 " in killed
    assert "2024-02-05" not in alive


def test_the_trial_log_says_what_was_seen_and_that_nothing_moved() -> None:
    assert "Rs 25,000 kill by Jan-Feb 2024" in TRIAL_LOG
    assert "No declared parameter, grid value or bar was changed" in TRIAL_LOG
