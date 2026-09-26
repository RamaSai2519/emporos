"""Every arm, its random-entry control, and the counts (EM-219 S1).

`ArmRunner.run` plays one arm on the window's signals, then `runs` control runs on the entries it
took, and gives the metrics and the control's p. `run_all` spreads the arms over worker processes
(forked, so each shares the loaded bars: nothing is pickled but the small results)."""

from __future__ import annotations

import multiprocessing
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date

from emporos.eventtrader.replay.records import Scenario
from emporos.research.s1.arms import Arm
from emporos.research.s1.control import RandomEntries
from emporos.research.s1.engine import BookEngine, RunResult
from emporos.research.s1.metrics import ArmMetrics, control_p, measure
from emporos.research.s1.signals import Signal

__all__ = ["ArmOutcome", "ArmRunner", "entries_per_month", "run_all"]

CONTROL_RUNS = 200


@dataclass(frozen=True)
class ArmOutcome:
    arm: Arm
    metrics: ArmMetrics
    control_p: float | None
    control_mean: float | None  # the control's mean net at adverse costs
    entries_by_month: dict[str, int]
    skipped: dict[str, int]
    signals: int


def entries_per_month(result: RunResult) -> dict[str, int]:
    counts: Counter[str] = Counter(t.day.strftime("%Y-%m") for t in result.trades)
    return dict(sorted(counts.items()))


class ArmRunner:
    def __init__(
        self,
        engine: BookEngine,
        sessions: Sequence[date],
        signals: Mapping[date, Sequence[Signal]],
        control: RandomEntries | None = None,
        runs: int = CONTROL_RUNS,
    ) -> None:
        self._engine, self._sessions, self._signals = engine, sessions, signals
        self._control, self._runs = control, runs

    def run(self, arm: Arm) -> ArmOutcome:
        result = self._engine.run(arm, self._sessions, self._signals)
        p: float | None = None
        mean: float | None = None
        if self._control is not None and result.trades:
            nets = [
                self._engine.run(
                    arm, self._sessions, self._control.signals(arm.name, i, result.trades)
                ).net(Scenario.ADVERSE)
                for i in range(self._runs)
            ]
            p = control_p(result.net(Scenario.ADVERSE), nets)
            mean = sum(nets) / len(nets)
        return ArmOutcome(
            arm, measure(result, self._sessions), p, mean, entries_per_month(result),
            dict(result.skipped), result.signals,
        )  # fmt: skip


_RUNNER: ArmRunner | None = None


def _work(arm: Arm) -> ArmOutcome:
    assert _RUNNER is not None
    return _RUNNER.run(arm)


def run_all(runner: ArmRunner, arms: Sequence[Arm], workers: int) -> list[ArmOutcome]:
    """The arms in order. With one worker, in this process; else forked workers."""
    global _RUNNER
    if workers <= 1:
        return [runner.run(arm) for arm in arms]
    _RUNNER = runner
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as pool:
        return list(pool.map(_work, arms))


def month_table(
    outcomes: Sequence[ArmOutcome], signals: Mapping[date, Sequence[Signal]]
) -> list[str]:
    """Signals and entries taken per month: the signals, then the fewest / median / most entries
    across the arms. Counts only."""
    signal_counts: dict[str, int] = defaultdict(int)
    for day, day_signals in signals.items():
        signal_counts[day.strftime("%Y-%m")] += len(day_signals)
    months = sorted(signal_counts)
    lines = ["month     signals   entries taken by the arms: min  median  max"]
    for month in months:
        taken = sorted(o.entries_by_month.get(month, 0) for o in outcomes)
        median = taken[len(taken) // 2] if taken else 0
        lines.append(
            f"{month}  {signal_counts[month]:>8,}   {taken[0] if taken else 0:>25}  {median:>6}  "
            f"{taken[-1] if taken else 0:>3}"
        )
    return lines
