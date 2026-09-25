"""Run every arm of a Track B cell and judge it (EM-230).

Each arm runs at BENCHMARK and at ADVERSE costs from the first session every rule has its history,
through the last Discovery day; its daily returns are block-bootstrapped for the §3.4 ruin numbers;
then the cell-level neighbour check is computed from the whole grid and each arm is judged."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.options.backtest import BacktestResult
from emporos.options.slippage import ADVERSE, BENCHMARK
from emporos.research.option_screen.b1 import (
    B1Arms,
    B1Backtests,
    B1Inputs,
    B1Parameters,
    warmup_end,
)
from emporos.research.option_screen.screen import ArmOutcome, OptionVerdict, judge
from emporos.research.option_screen.stats import OptionStats
from emporos.research.swing.bootstrap import BlockBootstrap

__all__ = ["ArmResult", "B1Screen", "daily_returns"]


@dataclass(frozen=True)
class ArmResult:
    arm: B1Parameters
    outcome: ArmOutcome
    neighbours: float | None
    verdict: OptionVerdict


def daily_returns(result: BacktestResult, capital: Decimal) -> list[float]:
    previous = capital
    out: list[float] = []
    for day in result.days:
        out.append(float(day.equity / previous - 1))
        previous = day.equity
    return out


class B1Screen:
    def __init__(
        self, inputs: B1Inputs, last_day: date, bootstrap: BlockBootstrap | None = None
    ) -> None:
        self._in = inputs
        self._last = last_day
        self._bootstrap = bootstrap or BlockBootstrap()
        self._backtests = B1Backtests(inputs)
        chain_days = inputs.chains.days()
        if not chain_days:
            raise ValueError("the chain source holds no days")
        self._first = max(chain_days[0], warmup_end(inputs.nifty_closes, inputs.vix_closes))

    @property
    def first_day(self) -> date:
        return self._first

    def run(self, arms: Sequence[B1Parameters]) -> list[ArmResult]:
        outcomes = {a.label: self._outcome(a) for a in arms}
        positive = {label: o.stats.net_pnl > 0 for label, o in outcomes.items()}
        adjacent = B1Arms.adjacent(arms)
        results: list[ArmResult] = []
        for arm in arms:
            near = adjacent[arm.label]
            share = sum(positive[n] for n in near) / len(near) if near else None
            verdict = judge(outcomes[arm.label], share, aggressive=arm.aggressive)
            results.append(ArmResult(arm, outcomes[arm.label], share, verdict))
        return results

    def _outcome(self, arm: B1Parameters) -> ArmOutcome:
        capital = self._in.capital
        bench = self._backtests.build(arm, BENCHMARK).run(self._first, self._last)
        adverse = self._backtests.build(arm, ADVERSE).run(self._first, self._last)
        return ArmOutcome(
            bench,
            OptionStats.of(bench, capital),
            OptionStats.of(adverse, capital),
            self._bootstrap.report(daily_returns(bench, capital)),
            self._first,
            capital,
        )
