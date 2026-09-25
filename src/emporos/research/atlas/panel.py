"""Bars on a session grid: one row a session, one column a 5-minute slot (EM-243).

Slot k is the bar that starts at 09:15 IST + 5k minutes (k = 0..74; slot 71 ends at 15:15). A slot
with no bar is NaN, never filled: a missing bar is missing, and every later step drops it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np

from emporos.core.clock import IST
from emporos.research.atlas.arrays import Floats
from emporos.research.atlas.series import BarSeries

__all__ = ["InstrumentPanel", "PanelBuilder", "SLOTS", "slot_end_minute"]

SLOTS = 75
FIRST_SLOT_MINUTE = 9 * 60 + 15
SLOT_MINUTES = 5


def slot_end_minute(slot: int) -> int:
    """Minutes after midnight IST at which the slot's bar closes."""
    return FIRST_SLOT_MINUTE + SLOT_MINUTES * (slot + 1)


@dataclass(frozen=True)
class InstrumentPanel:
    close: Floats  # (sessions, SLOTS)
    open0: Floats  # (sessions,) open of the session's first bar
    day_close: Floats  # (sessions,) close of the session's last bar


class PanelBuilder:
    def __init__(self, sessions: tuple[date, ...]) -> None:
        self._row = {day: i for i, day in enumerate(sessions)}
        self._size = len(sessions)

    def build(self, series: BarSeries) -> InstrumentPanel:
        close = np.full((self._size, SLOTS), np.nan)
        open0 = np.full(self._size, np.nan)
        day_close = np.full(self._size, np.nan)
        first_slot = np.full(self._size, SLOTS)
        for ts, opened, closed in zip(series.ts, series.open, series.close, strict=True):
            local = datetime.fromtimestamp(int(ts), IST)
            row = self._row.get(local.date())
            slot = (local.hour * 60 + local.minute - FIRST_SLOT_MINUTE) // SLOT_MINUTES
            if row is None or not 0 <= slot < SLOTS:
                continue
            close[row, slot] = closed
            if slot < first_slot[row]:
                first_slot[row], open0[row] = slot, opened
        for row in range(self._size):
            valid = np.flatnonzero(~np.isnan(close[row]))
            if len(valid):
                day_close[row] = close[row, valid[-1]]
        return InstrumentPanel(close, open0, day_close)
