"""A final holdout period the walk-forward pipeline never enters (EM-184), and the provenance
record of which date ranges a curation run's evidence actually came from.

`FinalHoldoutReservation.split` shrinks the range `WalkForwardPlanner.plan` is ever given to
`[start, walkable_end)`; the holdout `[walkable_end, end)` is returned purely for provenance
reporting, never handed to anything that fetches bars or plans windows. This is the same
"look-ahead prevented by the shape of the thing, not by discipline" principle `emporos.backtest.
feed`'s single-pass, unrewindable iterator already relies on: no parameter or hypothesis selection
CAN inspect the holdout, because nothing downstream of `split` is ever constructed with it in
scope — the reserved dates are simply never loaded into the process, not merely left unused by
convention.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from emporos.backtest.feed import FeedWindow
from emporos.backtest.walkforward import WalkForwardWindow


@dataclass(frozen=True)
class FinalHoldoutReservation:
    holdout_span: timedelta

    def __post_init__(self) -> None:
        if self.holdout_span <= timedelta(0):
            raise ValueError("a holdout needs a positive span")

    def split(self, start: datetime, end: datetime) -> tuple[FeedWindow, FeedWindow]:
        """`(walkable, holdout)`: only `walkable`'s range may ever be planned into walk-forward
        windows; `holdout` exists solely to be recorded, immutable, in a `CurationProvenance`."""
        walkable_end = end - self.holdout_span
        if walkable_end <= start:
            raise ValueError(
                f"a {self.holdout_span} holdout leaves nothing walkable in "
                f"{start.isoformat()}..{end.isoformat()}"
            )
        return FeedWindow(start, walkable_end), FeedWindow(walkable_end, end)


@dataclass(frozen=True)
class CurationProvenance:
    """Which date ranges a curation run's evidence came from: `research` spans every walk-forward
    window's TRAINING period (where parameters were chosen), `validation` spans every window's
    TEST period (what `Evidence` is built from — the out-of-sample results the verdict reads),
    `holdout` is the immutable final period `FinalHoldoutReservation` carved off before any window
    was planned. `research` and `validation` commonly overlap in a ROLLING walk-forward (a later
    window's training re-uses days an earlier window already tested) — expected, not a leak, since
    no window's train ever reaches into a LATER window's test or into the holdout at all."""

    research: FeedWindow
    validation: FeedWindow
    holdout: FeedWindow

    @classmethod
    def of(cls, windows: Sequence[WalkForwardWindow], holdout: FeedWindow) -> CurationProvenance:
        if not windows:
            raise ValueError("provenance needs at least one walk-forward window")
        research = FeedWindow(
            min(w.train.start for w in windows), max(w.train.end for w in windows)
        )
        validation = FeedWindow(
            min(w.test.start for w in windows), max(w.test.end for w in windows)
        )
        if validation.end > holdout.start:
            raise ValueError("a walk-forward test window reaches into the reserved holdout")
        return cls(research, validation, holdout)
