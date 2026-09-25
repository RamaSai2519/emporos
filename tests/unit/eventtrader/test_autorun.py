"""The unattended runner: order, resume, retry, halt, lock and the status file."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from emporos.core.clock import FixedClock
from emporos.eventtrader.autorun import (
    MAX_INCOMPLETE_ATTEMPTS,
    Autorun,
    Outcome,
    StepResult,
    already_running,
)

CLOCK = FixedClock(datetime(2026, 9, 26, 3, 10, 5, tzinfo=UTC))


class Scripted:
    def __init__(self, name: str, *results: StepResult | Exception) -> None:
        self.name, self._results, self.runs = name, list(results), 0

    def run(self) -> StepResult:
        self.runs += 1
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, Exception):
            raise result
        return result


DONE = StepResult(Outcome.DONE, "ok")


def autorun(tmp_path: Path, *steps: Scripted) -> Autorun:
    return Autorun(steps, tmp_path, CLOCK)


def status(tmp_path: Path) -> list[str]:
    return (tmp_path / "STATUS.md").read_text().splitlines()[1:]


def test_steps_run_in_order_and_a_finished_step_is_never_run_again(tmp_path: Path) -> None:
    a, b = Scripted("a", DONE), Scripted("b", DONE)

    assert autorun(tmp_path, a, b).run_once() == 0
    assert autorun(tmp_path, a, b).run_once() == 0  # a fresh process, the same state directory

    assert (a.runs, b.runs) == (1, 1)
    assert status(tmp_path)[0] == "- 2026-09-26T03:10:05Z | a | done | ok"
    assert status(tmp_path)[-1].endswith("ALL STEPS DONE")


def test_a_waiting_step_stops_the_firing_and_is_asked_again_next_time(tmp_path: Path) -> None:
    wait = Scripted("snapshot", StepResult(Outcome.WAIT, "70 material filings still pending"), DONE)
    later = Scripted("smoke", DONE)

    assert autorun(tmp_path, wait, later).run_once() == 0
    assert later.runs == 0 and "waiting | 70 material" in status(tmp_path)[0]
    assert autorun(tmp_path, wait, later).run_once() == 0
    assert later.runs == 1 and wait.runs == 2


def test_an_incomplete_step_is_retried_and_never_skipped_then_halts(tmp_path: Path) -> None:
    incomplete = Scripted("v1", StepResult(Outcome.INCOMPLETE, "5% stage errors"))
    after = Scripted("v2", DONE)

    for attempt in range(1, MAX_INCOMPLETE_ATTEMPTS):
        assert autorun(tmp_path, incomplete, after).run_once() == 0
        assert f"incomplete (attempt {attempt}, will retry)" in status(tmp_path)[-1]
    assert autorun(tmp_path, incomplete, after).run_once() == 1

    assert after.runs == 0 and "HALTED" in status(tmp_path)[-1]


def test_a_failure_or_an_exception_halts_and_a_halted_run_does_nothing_until_cleared(
    tmp_path: Path,
) -> None:
    boom, after = Scripted("smoke", RuntimeError("401 unauthorised")), Scripted("v1", DONE)

    assert autorun(tmp_path, boom, after).run_once() == 1
    assert autorun(tmp_path, boom, after).run_once() == 1

    assert (boom.runs, after.runs) == (1, 0)
    assert "RuntimeError: 401 unauthorised" in status(tmp_path)[-1]
    assert "smoke: RuntimeError" in (tmp_path / "state.json").read_text()


def test_a_second_firing_while_one_runs_does_nothing(tmp_path: Path) -> None:
    class Reentrant(Scripted):
        def run(self) -> StepResult:
            assert already_running(tmp_path)
            assert autorun(tmp_path, Scripted("x", DONE)).run_once() == 0  # locked out
            return super().run()

    inner = Reentrant("a", DONE)
    assert autorun(tmp_path, inner).run_once() == 0
    assert not already_running(tmp_path) and inner.runs == 1
