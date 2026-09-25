"""How much of a crossing's direction carried on, and how sure that is (EM-243).

Every figure is over the crossings given, all of them (the no-cause baseline every cause class is
judged against later). The t statistic clusters by day: crossings on one day share the market's
weather, so a day counts once, not once per name."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np

from emporos.research.atlas.arrays import Floats

__all__ = ["Continuation", "clustered_mean_t"]


@dataclass(frozen=True)
class Continuation:
    n: int
    mean: float  # percent, in the crossing's direction
    hit: float  # share of crossings that continued (> 0)
    t: float  # day-clustered

    def line(self) -> str:
        if self.n == 0:
            return "n=0"
        return f"n={self.n:>7,} mean {self.mean:+.3f}% hit {self.hit:5.1%} t {self.t:+.2f}"


def clustered_mean_t(values: Floats, days: list[date]) -> Continuation:
    """Mean of the non-NaN values, the share above zero, and the mean over its day-clustered
    standard error (the sum of each day's deviations, squared, over the count)."""
    keep = ~np.isnan(values)
    x = values[keep]
    if len(x) == 0:
        return Continuation(0, float("nan"), float("nan"), float("nan"))
    mean = float(x.mean())
    kept_days = [d for d, ok in zip(days, keep, strict=True) if ok]
    by_day: dict[date, float] = defaultdict(float)
    for d, deviation in zip(kept_days, x - mean, strict=True):
        by_day[d] += float(deviation)
    se = float(np.sqrt(sum(v * v for v in by_day.values()))) / len(x)
    t = mean / se if se > 0 else float("nan")
    return Continuation(len(x), mean, float((x > 0).mean()), t)
