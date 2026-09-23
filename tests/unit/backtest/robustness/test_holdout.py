"""EM-184: `FinalHoldoutReservation` (a date range the walk-forward pipeline never enters) and
`CurationProvenance` (which date ranges a curation run's evidence came from)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from emporos.backtest.feed import FeedWindow
from emporos.backtest.robustness.holdout import CurationProvenance, FinalHoldoutReservation
from emporos.backtest.walkforward import WalkForwardWindow

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 4, 1, tzinfo=UTC)  # 90 days


def test_holdout_span_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        FinalHoldoutReservation(timedelta(0))


def test_split_reserves_exactly_the_configured_span_at_the_end() -> None:
    reservation = FinalHoldoutReservation(timedelta(days=30))

    walkable, holdout = reservation.split(START, END)

    assert walkable == FeedWindow(START, END - timedelta(days=30))
    assert holdout == FeedWindow(END - timedelta(days=30), END)
    # the two windows exactly tile the original range, no gap and no overlap
    assert walkable.end == holdout.start


def test_a_holdout_that_would_swallow_the_whole_range_is_refused() -> None:
    reservation = FinalHoldoutReservation(timedelta(days=200))

    with pytest.raises(ValueError, match="leaves nothing walkable"):
        reservation.split(START, END)


def _window(index: int, train: FeedWindow, test: FeedWindow) -> WalkForwardWindow:
    return WalkForwardWindow(index, train, test)


class TestCurationProvenance:
    def test_needs_at_least_one_window(self) -> None:
        holdout = FeedWindow(END - timedelta(days=30), END)

        with pytest.raises(ValueError, match="at least one"):
            CurationProvenance.of([], holdout)

    def test_research_and_validation_span_every_windows_train_and_test(self) -> None:
        windows = [
            _window(
                0,
                FeedWindow(START, START + timedelta(days=20)),
                FeedWindow(START + timedelta(days=20), START + timedelta(days=30)),
            ),
            _window(
                1,
                FeedWindow(START + timedelta(days=10), START + timedelta(days=30)),
                FeedWindow(START + timedelta(days=30), START + timedelta(days=40)),
            ),
        ]
        holdout = FeedWindow(START + timedelta(days=40), END)

        provenance = CurationProvenance.of(windows, holdout)

        assert provenance.research == FeedWindow(START, START + timedelta(days=30))
        assert provenance.validation == FeedWindow(
            START + timedelta(days=20), START + timedelta(days=40)
        )
        assert provenance.holdout == holdout

    def test_a_test_window_reaching_into_the_holdout_is_refused(self) -> None:
        windows = [
            _window(
                0,
                FeedWindow(START, START + timedelta(days=20)),
                FeedWindow(START + timedelta(days=20), START + timedelta(days=50)),
            )
        ]
        holdout = FeedWindow(START + timedelta(days=40), END)  # overlaps the test window above

        with pytest.raises(ValueError, match="reaches into the reserved holdout"):
            CurationProvenance.of(windows, holdout)
