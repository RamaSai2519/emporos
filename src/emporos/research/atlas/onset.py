"""When did a move start, when did it peak, and what came after (EM-243, plan §2).

Onset is the first 5-minute bar at which the day's cumulative residual has passed a quarter of its
final value, or the OPEN if the overnight gap's residual alone was at least half of it. What follows
the onset is measured from the onset bar's close (from the open for an overnight onset)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from emporos.research.atlas.arrays import Floats
from emporos.research.atlas.events import OnsetKind
from emporos.research.atlas.panel import SLOTS

__all__ = ["Onset", "OnsetAnalyzer"]

CLOSE_SLOT = 71  # the bar that ends at 15:15
AFTER_15M, AFTER_60M = 3, 12  # bars


@dataclass(frozen=True)
class Onset:
    kind: OnsetKind
    slot: int  # the bar the onset is at the end of; -1 for the open
    peak_slot: int
    after_15m: float
    after_60m: float
    after_close: float


NOTHING = Onset(OnsetKind.NONE, -1, -1, np.nan, np.nan, np.nan)


class OnsetAnalyzer:
    def __init__(self, onset_share: float = 0.25, overnight_share: float = 0.5) -> None:
        self._onset, self._overnight = onset_share, overnight_share

    def daily(self, path: Floats, gap_resid: float, direction: int) -> Onset:
        """The day's move: the path is the residual's, direction the sign of the day's residual."""
        if np.isnan(path).all() or np.isnan(gap_resid):
            return NOTHING
        signed = direction * path
        final = signed[np.flatnonzero(~np.isnan(signed))[-1]]
        if final <= 0:
            return NOTHING  # the bars' path ended against the day's residual: no onset to place
        if direction * gap_resid >= self._overnight * final:
            return self._after(path, gap_resid, -1, direction, OnsetKind.OVERNIGHT)
        slot = int(np.flatnonzero(signed >= self._onset * final)[0])
        return self._after(path, gap_resid, slot, direction, OnsetKind.INTRADAY)

    def jump(self, path: Floats, gap_resid: float, window_start: int, direction: int) -> Onset:
        """A 15-minute jump: known at the window's last bar."""
        if np.isnan(path).all() or np.isnan(gap_resid):
            return NOTHING
        return self._after(path, gap_resid, window_start + 2, direction, OnsetKind.JUMP)

    @staticmethod
    def _after(path: Floats, gap_resid: float, slot: int, direction: int, kind: OnsetKind) -> Onset:
        base = gap_resid if slot < 0 else path[slot]
        signed = np.where(np.isnan(path), -np.inf, direction * path)
        peak = int(np.argmax(signed))

        def later(bars: int) -> float:
            target = slot + bars
            return float(path[target] - base) if 0 <= target < SLOTS else float("nan")

        close = float(path[CLOSE_SLOT] - base) if slot < CLOSE_SLOT else float("nan")
        return Onset(kind, slot, peak, later(AFTER_15M), later(AFTER_60M), close)
