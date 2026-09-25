"""A series' returns at the three scales the ledger uses, from its panel (EM-243).

* `daily`: last close to last close, only when the previous session traded too;
* `gap`: the session's first open over the previous session's last close;
* `bars`: each 5-minute bar's own return (the first bar's is from the session's open);
* `level`: the daily close, or for a group index a compounded level, so a return over any span is
  one ratio."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from emporos.research.atlas.arrays import Floats
from emporos.research.atlas.panel import InstrumentPanel

__all__ = ["Returns", "returns_of"]


@dataclass(frozen=True)
class Returns:
    daily: Floats  # (S,)
    gap: Floats  # (S,)
    bars: Floats  # (S, SLOTS)
    level: Floats  # (S,)
    close: Floats  # (S, SLOTS) bar closes (NaN for a group index, which has no prices)
    open0: Floats  # (S,) the session's first open


def returns_of(panel: InstrumentPanel) -> Returns:
    previous = np.roll(panel.day_close, 1)
    previous[0] = np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        daily = panel.day_close / previous - 1
        gap = panel.open0 / previous - 1
        before = np.concatenate([panel.open0[:, None], panel.close[:, :-1]], axis=1)
        bars = panel.close / before - 1
    for array in (daily, gap, bars):
        array[~np.isfinite(array)] = np.nan
    return Returns(daily, gap, bars, panel.day_close.copy(), panel.close, panel.open0)
