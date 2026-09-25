"""Risk of ruin by block bootstrap (EM-226, PROFIT_PLAN.md §3.4), for any track's daily P&L.

Resample the arm's own daily P&L in blocks (so streaks and volatility clusters survive), string
`horizon_days` of it into a path from the starting capital, and count the paths that draw down by at
least `drawdown` from their running peak and the paths that end the year below where they began. An
aggressive arm passes only with P(drawdown) at or under 5%. The random source is injected and
seeded, so a report is reproducible; the sample is only as informative as the history it
resamples, and a history with no bad month cannot show one."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

__all__ = ["BlockBootstrapRuin", "RuinReport"]


@dataclass(frozen=True)
class RuinReport:
    paths: int
    horizon_days: int
    block_days: int
    drawdown: Decimal  # the fall that counts as ruin, as a fraction of the running peak
    p_drawdown: Decimal  # share of paths that fell that far
    p_negative: Decimal  # share of paths that ended below the starting capital


class BlockBootstrapRuin:
    def __init__(
        self,
        capital: Decimal,
        rng: random.Random,
        drawdown: Decimal = Decimal("0.30"),
        paths: int = 10_000,
        horizon_days: int = 252,
        block_days: int = 20,
    ) -> None:
        if capital <= 0:
            raise ValueError("capital must be positive")
        if not Decimal(0) < drawdown < Decimal(1):
            raise ValueError("the ruin drawdown must be between 0 and 1")
        if paths < 1 or horizon_days < 1 or block_days < 1:
            raise ValueError("paths, horizon and block length must be positive")
        self._capital = capital
        self._rng = rng
        self._drawdown = drawdown
        self._paths = paths
        self._horizon = horizon_days
        self._block = block_days

    def assess(self, daily_pnl: Sequence[Decimal]) -> RuinReport:
        if len(daily_pnl) < self._block:
            raise ValueError(f"need at least {self._block} days of P&L, got {len(daily_pnl)}")
        ruined = negative = 0
        starts = len(daily_pnl) - self._block + 1
        for _ in range(self._paths):
            equity = peak = self._capital
            fell = False
            days = 0
            while days < self._horizon:
                begin = self._rng.randrange(starts)
                for pnl in daily_pnl[begin : begin + self._block]:
                    if days == self._horizon:
                        break
                    equity += pnl
                    days += 1
                    peak = max(peak, equity)
                    if peak - equity >= self._drawdown * peak:
                        fell = True
            ruined += fell
            negative += equity < self._capital
        total = Decimal(self._paths)
        return RuinReport(
            self._paths, self._horizon, self._block, self._drawdown,
            Decimal(ruined) / total, Decimal(negative) / total,
        )  # fmt: skip
