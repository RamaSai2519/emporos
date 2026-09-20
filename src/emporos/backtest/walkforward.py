"""Walk-forward windows with a strict temporal boundary (plan.md §10).

    ROLLING    train [t, t+T)  test [t+T+e, t+T+e+S)      t += step        (train slides)
    ANCHORED   train [a, a+T+k*step)  test [end+e, +S)                     (train grows from a)

A window is only ever CONSTRUCTED with its test period entirely after its training period; an
overlap is a `LeakageError` at that point, so no code path can hold a leaky window and report a
flattering number from it. Test windows must not overlap each other either: a bar scored in two
tests would be counted twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import pairwise

from emporos.backtest.feed import FeedWindow

ZERO = timedelta(0)


class LeakageError(ValueError):
    """A test period overlaps the data the strategy was tuned on (or another test period)."""


class WalkForwardMode(StrEnum):
    ROLLING = "ROLLING"
    ANCHORED = "ANCHORED"


@dataclass(frozen=True)
class WalkForwardWindow:
    index: int
    train: FeedWindow
    test: FeedWindow

    def __post_init__(self) -> None:
        if self.test.start < self.train.end:
            raise LeakageError(
                f"window {self.index}: the test period starts {self.test.start.isoformat()}, "
                f"before the training period ends {self.train.end.isoformat()}"
            )


class WalkForwardPlanner:
    def plan(
        self,
        mode: WalkForwardMode,
        start: datetime,
        end: datetime,
        train: timedelta,
        test: timedelta,
        step: timedelta | None = None,
        embargo: timedelta = ZERO,
    ) -> tuple[WalkForwardWindow, ...]:
        """Every window that fits in [start, end). `step` defaults to the test span, so the test
        periods tile the data without overlap; a smaller step would overlap them and is refused.
        `embargo` leaves a gap between training and test."""
        step = test if step is None else step
        if train <= ZERO or test <= ZERO or step <= ZERO:
            raise ValueError("train, test and step must be positive spans")
        if embargo < ZERO:
            raise LeakageError("a negative embargo would put test data inside the training period")
        if step < test:
            raise LeakageError("a step shorter than the test span makes consecutive tests overlap")
        windows: list[WalkForwardWindow] = []
        train_end = start + train
        while train_end + embargo + test <= end:
            test_start = train_end + embargo
            train_start = start if mode is WalkForwardMode.ANCHORED else train_end - train
            windows.append(
                WalkForwardWindow(
                    len(windows),
                    FeedWindow(train_start, train_end),
                    FeedWindow(test_start, test_start + test),
                )
            )
            train_end += step
        if not windows:
            raise ValueError("the data span is too short for even one train + test window")
        return tuple(windows)


class WindowSequenceCheck:
    """Windows that were built by hand (or edited) are checked as a set before anything runs."""

    def verify(self, windows: Sequence[WalkForwardWindow]) -> None:
        if not windows:
            raise ValueError("there are no walk-forward windows to run")
        for earlier, later in pairwise(windows):
            if later.test.start < earlier.test.end:
                raise LeakageError(
                    f"test periods {earlier.index} and {later.index} overlap: "
                    "a period would be scored twice"
                )
