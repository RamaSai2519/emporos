"""EM-107: window planning and leakage detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from emporos.backtest.feed import FeedWindow
from emporos.backtest.walkforward import (
    LeakageError,
    WalkForwardMode,
    WalkForwardPlanner,
    WalkForwardWindow,
    WindowSequenceCheck,
)

D = timedelta(days=1)
START = datetime(2026, 1, 1, tzinfo=UTC)


def at(day: int) -> datetime:
    return START + day * D


def bounds(windows: tuple[WalkForwardWindow, ...]) -> list[tuple[int, int, int, int]]:
    def day(when: datetime) -> int:
        return (when - START).days

    return [
        (day(w.train.start), day(w.train.end), day(w.test.start), day(w.test.end)) for w in windows
    ]


class TestPlanning:
    def test_rolling_slides_the_training_window_and_tiles_the_tests(self) -> None:
        windows = WalkForwardPlanner().plan(WalkForwardMode.ROLLING, at(0), at(21), 10 * D, 5 * D)

        assert bounds(windows) == [(0, 10, 10, 15), (5, 15, 15, 20)]
        assert [w.index for w in windows] == [0, 1]

    def test_anchored_keeps_the_start_and_grows_the_training_window(self) -> None:
        windows = WalkForwardPlanner().plan(WalkForwardMode.ANCHORED, at(0), at(21), 10 * D, 5 * D)

        assert bounds(windows) == [(0, 10, 10, 15), (0, 15, 15, 20)]

    def test_an_embargo_leaves_a_gap_between_training_and_test(self) -> None:
        windows = WalkForwardPlanner().plan(
            WalkForwardMode.ROLLING, at(0), at(22), 10 * D, 5 * D, embargo=D
        )

        assert bounds(windows) == [(0, 10, 11, 16), (5, 15, 16, 21)]

    def test_a_larger_step_leaves_untested_gaps_but_never_overlaps(self) -> None:
        windows = WalkForwardPlanner().plan(
            WalkForwardMode.ROLLING, at(0), at(40), 10 * D, 5 * D, step=8 * D
        )

        assert bounds(windows) == [
            (0, 10, 10, 15),
            (8, 18, 18, 23),
            (16, 26, 26, 31),
            (24, 34, 34, 39),
        ]

    def test_the_last_window_must_fit_inside_the_data(self) -> None:
        windows = WalkForwardPlanner().plan(WalkForwardMode.ROLLING, at(0), at(19), 10 * D, 5 * D)
        assert bounds(windows) == [(0, 10, 10, 15)]  # the second would end on day 20

    def test_too_little_data_for_one_window(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            WalkForwardPlanner().plan(WalkForwardMode.ROLLING, at(0), at(12), 10 * D, 5 * D)


class TestLeakageIsRefused:
    def test_a_step_shorter_than_the_test_span_would_score_bars_twice(self) -> None:
        with pytest.raises(LeakageError, match="overlap"):
            WalkForwardPlanner().plan(
                WalkForwardMode.ROLLING, at(0), at(40), 10 * D, 5 * D, step=3 * D
            )

    def test_a_negative_embargo_would_put_test_data_in_training(self) -> None:
        with pytest.raises(LeakageError, match="negative embargo"):
            WalkForwardPlanner().plan(
                WalkForwardMode.ANCHORED, at(0), at(40), 10 * D, 5 * D, embargo=-D
            )

    def test_a_window_whose_test_starts_before_training_ends_cannot_be_built(self) -> None:
        with pytest.raises(LeakageError, match="before the training period ends"):
            WalkForwardWindow(0, FeedWindow(at(0), at(10)), FeedWindow(at(9), at(14)))

    def test_a_test_window_entirely_before_training_cannot_be_built(self) -> None:
        with pytest.raises(LeakageError):
            WalkForwardWindow(0, FeedWindow(at(10), at(20)), FeedWindow(at(0), at(5)))

    def test_a_test_that_starts_exactly_when_training_ends_is_fine(self) -> None:
        window = WalkForwardWindow(0, FeedWindow(at(0), at(10)), FeedWindow(at(10), at(15)))
        assert window.test.start == window.train.end

    def test_overlapping_test_periods_across_windows_are_refused(self) -> None:
        first = WalkForwardWindow(0, FeedWindow(at(0), at(10)), FeedWindow(at(10), at(15)))
        second = WalkForwardWindow(1, FeedWindow(at(3), at(13)), FeedWindow(at(14), at(19)))

        with pytest.raises(LeakageError, match="scored twice"):
            WindowSequenceCheck().verify([first, second])

    def test_adjacent_test_periods_and_gaps_pass_and_nothing_at_all_does_not(self) -> None:
        a = WalkForwardWindow(0, FeedWindow(at(0), at(10)), FeedWindow(at(10), at(15)))
        b = WalkForwardWindow(1, FeedWindow(at(5), at(15)), FeedWindow(at(15), at(20)))
        c = WalkForwardWindow(2, FeedWindow(at(10), at(20)), FeedWindow(at(22), at(25)))
        WindowSequenceCheck().verify([a, b, c])
        with pytest.raises(ValueError):
            WindowSequenceCheck().verify([])

    @pytest.mark.parametrize("bad", [(0, 5, 5), (10, 0, 5), (10, 5, 0)])
    def test_spans_must_be_positive(self, bad: tuple[int, int, int]) -> None:
        train, test, step = bad
        with pytest.raises(ValueError):
            WalkForwardPlanner().plan(
                WalkForwardMode.ROLLING, at(0), at(99), train * D, test * D, step * D
            )


@settings(max_examples=150, deadline=None)
@given(
    mode=st.sampled_from(list(WalkForwardMode)),
    span=st.integers(min_value=20, max_value=200),
    train=st.integers(min_value=1, max_value=30),
    test=st.integers(min_value=1, max_value=15),
    extra=st.integers(min_value=0, max_value=10),
    embargo=st.integers(min_value=0, max_value=5),
)
def test_any_plan_keeps_test_after_train_tests_disjoint_and_everything_inside_the_data(
    mode: WalkForwardMode, span: int, train: int, test: int, extra: int, embargo: int
) -> None:
    try:
        windows = WalkForwardPlanner().plan(
            mode, at(0), at(span), train * D, test * D, step=(test + extra) * D, embargo=embargo * D
        )
    except ValueError:
        return  # too little data for these spans: refused, which is the point
    for window in windows:
        assert window.train.end + embargo * D <= window.test.start
        assert at(0) <= window.train.start < window.train.end and window.test.end <= at(span)
        assert window.test.end - window.test.start == test * D
        if mode is WalkForwardMode.ANCHORED:
            assert window.train.start == at(0)
    for earlier, later in pairwise(windows):
        assert earlier.test.end <= later.test.start
    WindowSequenceCheck().verify(windows)
