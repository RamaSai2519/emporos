"""Risk of ruin by block bootstrap of an arm's daily returns (PROFIT_PLAN §3.4, EM-223).

Resample the arm's daily returns in blocks (20 sessions, keeping their serial dependence), chain
blocks into 12-month paths (252 sessions), and report how often a path (a) falls 30% or more from a
peak, and (b) ends the year below where it started. 10,000 paths. The generator is seeded, so the
same returns give the same answer on any machine; the seed is part of the report.

A block starts anywhere in the series that leaves room for a whole block (a circular wrap would
invent a jump from the last day to the first). A series shorter than one block cannot be
bootstrapped and raises. An aggressive arm passes only with P(drawdown >= 30%) <= 5% (§3.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from random import Random

__all__ = ["BlockBootstrap", "RuinReport"]

RUIN_DRAWDOWN = 0.30
AGGRESSIVE_MAX_P_DRAWDOWN = 0.05


@dataclass(frozen=True)
class RuinReport:
    paths: int
    block: int
    horizon: int
    seed: int
    p_drawdown_30: float
    p_year_negative: float
    median_year_return: float

    @property
    def passes_aggressive_bar(self) -> bool:
        return self.p_drawdown_30 <= AGGRESSIVE_MAX_P_DRAWDOWN


class BlockBootstrap:
    def __init__(
        self, paths: int = 10_000, block: int = 20, horizon: int = 252, seed: int = 20260925
    ) -> None:
        if paths < 1 or block < 1 or horizon < 1:
            raise ValueError("paths, block and horizon are positive")
        self._paths = paths
        self._block = block
        self._horizon = horizon
        self._seed = seed

    def report(self, daily_returns: Sequence[float]) -> RuinReport:
        if len(daily_returns) < self._block:
            raise ValueError(f"need at least {self._block} sessions to bootstrap in blocks")
        rng = Random(self._seed)
        starts = len(daily_returns) - self._block + 1
        deep, negative = 0, 0
        finals: list[float] = []
        for _ in range(self._paths):
            equity, peak, worst = 1.0, 1.0, 0.0
            taken = 0
            while taken < self._horizon:
                start = rng.randrange(starts)
                for r in daily_returns[start : start + self._block][: self._horizon - taken]:
                    equity *= 1 + r
                    peak = max(peak, equity)
                    worst = max(worst, 1 - equity / peak)
                    taken += 1
            deep += worst >= RUIN_DRAWDOWN
            negative += equity < 1.0
            finals.append(equity - 1)
        finals.sort()
        return RuinReport(
            self._paths, self._block, self._horizon, self._seed,
            deep / self._paths, negative / self._paths, finals[len(finals) // 2],
        )  # fmt: skip
