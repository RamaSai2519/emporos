"""What an arm's run is judged on (declaration `falsification`), and the control's p-value."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

from emporos.eventtrader.replay.records import Scenario
from emporos.research.s1.engine import RunResult

__all__ = ["ArmMetrics", "Bars", "control_p", "measure"]


@dataclass(frozen=True)
class Bars:
    """The Dev bars of `falsification`, fixed before any run."""

    min_trades: int = 100
    min_t: float = 3.0
    max_drawdown: float = 15_000.0
    min_months_positive: float = 0.60
    max_control_p: float = 0.05


@dataclass(frozen=True)
class ArmMetrics:
    trades: int
    net_benchmark: float
    net_adverse: float
    win_rate: float
    daily_t: float  # t of the daily net P&L at ADVERSE costs, over every session of the window
    max_drawdown: float  # rupees, on the daily adverse curve
    months_positive: float  # share of months with trades that are net positive at ADVERSE
    months_with_trades: int
    killed_on: date | None

    def failures(self, bars: Bars, control_p_value: float | None) -> list[str]:
        out: list[str] = []
        if self.net_adverse <= 0:
            out.append("net at adverse costs is not above zero")
        if not self.daily_t >= bars.min_t:
            out.append(f"daily t {self.daily_t:.2f} below {bars.min_t}")
        if self.trades < bars.min_trades:
            out.append(f"{self.trades} trades, fewer than {bars.min_trades}")
        if self.max_drawdown >= bars.max_drawdown:
            out.append(f"max drawdown Rs {self.max_drawdown:,.0f}")
        if self.killed_on is not None:
            out.append(f"killed on {self.killed_on}")
        if self.months_positive < bars.min_months_positive:
            out.append(f"{self.months_positive:.0%} of months positive")
        if control_p_value is None or control_p_value >= bars.max_control_p:
            out.append(f"control p {control_p_value if control_p_value is not None else 'n/a'}")
        return out


def measure(result: RunResult, sessions: Sequence[date]) -> ArmMetrics:
    daily: dict[date, float] = defaultdict(float)
    months: dict[tuple[int, int], float] = defaultdict(float)
    for trade in result.trades:
        net = trade.net(Scenario.ADVERSE)
        daily[trade.day] += net
        months[(trade.day.year, trade.day.month)] += net
    series = np.array([daily.get(d, 0.0) for d in sessions])
    std = float(series.std(ddof=1)) if len(series) > 1 else 0.0
    t = float(series.mean()) / (std / math.sqrt(len(series))) if std > 0 else float("nan")
    curve = np.cumsum(series)
    drawdown = float((np.maximum.accumulate(curve) - curve).max()) if len(curve) else 0.0
    wins = sum(1 for tr in result.trades if tr.net(Scenario.ADVERSE) > 0)
    positive = sum(1 for v in months.values() if v > 0)
    return ArmMetrics(
        len(result.trades), result.net(Scenario.BENCHMARK), result.net(Scenario.ADVERSE),
        wins / len(result.trades) if result.trades else 0.0, t, drawdown,
        positive / len(months) if months else 0.0, len(months), result.killed_on,
    )  # fmt: skip


def control_p(arm_net: float, control_nets: Sequence[float]) -> float:
    """p = (1 + runs whose net is at least the arm's) / (1 + runs)."""
    return (1 + sum(1 for n in control_nets if n >= arm_net)) / (1 + len(control_nets))
