"""The numbers PROFIT_PLAN §12.5 judges a run by, from a `RunResult` (EM-240).

Everything is net of costs AND of the models' token cost: a day's P&L is its trades' net (by the
day each closed) minus the token cost of the decisions taken that day. Trade P&L is in rupees;
nothing here is annualised."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from random import Random

from emporos.core.clock import IST
from emporos.eventtrader.replay.engine import RunResult
from emporos.eventtrader.replay.records import Scenario

__all__ = ["Bar", "LossBootstrap", "ScenarioMetrics", "Section125Bars", "metrics_for"]

LOSS_BUDGET = 25_000.0
MAX_DRAWDOWN = Decimal(15_000)


@dataclass(frozen=True)
class ScenarioMetrics:
    scenario: Scenario
    trades: int
    net_trading: Decimal  # trades' net of costs, before token cost
    token_cost: Decimal
    daily: tuple[tuple[date, Decimal], ...]  # net of both, by calendar day
    wins: int
    months_with_trades: int
    months_positive: int
    top_name: str | None
    top_name_share: float | None  # of the net trading profit; None when there is no profit

    @property
    def net_total(self) -> Decimal:
        return self.net_trading - self.token_cost

    @property
    def expectancy(self) -> Decimal | None:
        """Net per trade, after the token cost of the whole run."""
        return self.net_total / self.trades if self.trades else None

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.trades if self.trades else None

    @property
    def daily_t(self) -> float | None:
        values = [float(v) for _, v in self.daily]
        if len(values) < 2:
            return None
        spread = statistics.stdev(values)
        if spread == 0:
            return None
        return statistics.fmean(values) / (spread / math.sqrt(len(values)))

    @property
    def max_drawdown(self) -> Decimal:
        peak = equity = worst = Decimal(0)
        for _, value in self.daily:
            equity += value
            peak = max(peak, equity)
            worst = max(worst, peak - equity)
        return worst

    @property
    def months_positive_share(self) -> float | None:
        return self.months_positive / self.months_with_trades if self.months_with_trades else None


def metrics_for(result: RunResult, scenario: Scenario, sessions: Sequence[date]) -> ScenarioMetrics:
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    for day in sessions:
        by_day[day] += Decimal(0)
    by_month: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    by_name: dict[str, Decimal] = defaultdict(Decimal)
    for trade in result.trades:
        net = trade.net(scenario)
        closed = trade.exit_ts.astimezone(IST).date()
        by_day[closed] += net
        by_month[(closed.year, closed.month)] += net
        by_name[trade.symbol] += net
    for day, cost in result.stats.token_cost_by_day.items():
        by_day[day] -= cost
        if (day.year, day.month) in by_month:
            by_month[(day.year, day.month)] -= cost
    total = sum(by_name.values(), Decimal(0))
    top = max(by_name.items(), key=lambda kv: kv[1]) if by_name else None
    return ScenarioMetrics(
        scenario, len(result.trades), sum((t.net(scenario) for t in result.trades), Decimal(0)),
        result.token_cost_inr, tuple(sorted(by_day.items())),
        sum(1 for t in result.trades if t.net(scenario) > 0), len(by_month),
        sum(1 for v in by_month.values() if v > 0), top[0] if top else None,
        float(top[1] / total) if top and total > 0 else None,
    )  # fmt: skip


class LossBootstrap:
    """P(losing Rs 25,000 within 12 months) from the run's daily net P&L: 20-session blocks (serial
    dependence kept), chained into 252-session paths, 10,000 paths, seeded. A path loses when its
    running total, from its own start, reaches -Rs 25,000 at any point."""

    def __init__(
        self,
        paths: int = 10_000,
        block: int = 20,
        horizon: int = 252,
        seed: int = 20260925,
        loss: float = LOSS_BUDGET,
    ) -> None:
        if paths < 1 or block < 1 or horizon < 1 or loss <= 0:
            raise ValueError("paths, block, horizon and the loss are positive")
        self._paths, self._block, self._horizon = paths, block, horizon
        self._seed, self._loss = seed, loss

    def p_loss(self, daily: Sequence[Decimal]) -> float | None:
        """None when the run has fewer sessions than one block."""
        values = [float(v) for v in daily]
        if len(values) < self._block:
            return None
        rng = Random(self._seed)
        starts = len(values) - self._block + 1
        lost = 0
        for _ in range(self._paths):
            total, taken, hit = 0.0, 0, False
            while taken < self._horizon and not hit:
                start = rng.randrange(starts)
                for v in values[start : start + self._block][: self._horizon - taken]:
                    total += v
                    taken += 1
                    if total <= -self._loss:
                        hit = True
                        break
            lost += hit
        return lost / self._paths


@dataclass(frozen=True)
class Bar:
    name: str
    passed: bool
    value: str
    threshold: str


class Section125Bars:
    """The bars, each evaluated and reported, none short-circuited. Net at ADVERSE costs and net
    expectancy at BENCHMARK costs are the two the plan names; every other bar is judged at the
    BENCHMARK scenario, and the report also gives the adverse figures."""

    def evaluate(
        self,
        benchmark: ScenarioMetrics,
        adverse: ScenarioMetrics,
        p_loss: float | None,
        control_p: float | None,
    ) -> list[Bar]:
        expectancy, t = benchmark.expectancy, benchmark.daily_t
        months, share = benchmark.months_positive_share, benchmark.top_name_share
        return [
            Bar("net P&L at adverse costs > 0", adverse.net_total > 0,
                _n(adverse.net_total), "> 0"),
            Bar("net expectancy per trade at benchmark > 0",
                expectancy is not None and expectancy > 0, _n(expectancy), "> 0"),
            Bar("trades", benchmark.trades >= 60, str(benchmark.trades), ">= 60"),
            Bar("t of daily net P&L", t is not None and t >= 2.0, _n(t, ".2f"), ">= 2.0"),
            Bar("max drawdown", benchmark.max_drawdown < MAX_DRAWDOWN,
                _n(benchmark.max_drawdown), "< 15000"),
            Bar("P(losing Rs 25,000 in 12 months)", p_loss is not None and p_loss <= 0.10,
                _n(p_loss, ".3f"), "<= 0.10"),
            Bar("months with trades net positive", months is not None and months >= 0.55,
                _n(months, ".2f"), ">= 0.55"),
            Bar("largest name's share of net profit", share is not None and share <= 0.30,
                _n(share, ".2f"), "<= 0.30"),
            Bar("coin-flip control beaten", control_p is not None and control_p < 0.05,
                _n(control_p, ".4f"), "p < 0.05"),
        ]  # fmt: skip


def _n(value: Decimal | float | None, spec: str = ".0f") -> str:
    return "n/a" if value is None else format(value, spec)
