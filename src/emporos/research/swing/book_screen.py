"""The combined-book screen: a book of sleeves through the §3.2 bar (EM-233, cell A4).

`SleeveWorld` is one sleeve's data, its strategy recipe and its own same-universe benchmark.
`BookScreenRun` simulates each sleeve on its share of the book's capital (at BENCHMARK costs and at
ADVERSE costs), combines them at the split with `SleeveCombiner` (a quarterly reset, the transfer
costed), builds the benchmark the same way from the sleeves' own benchmarks at the same weights, and
returns the ordinary `ArmOutcome` for the book (the §3.4 bootstrap on the book's daily returns) plus
what only a book has: the sleeves' runs, the transfers and the sleeves' monthly correlation.

The concentration check is on single stocks: an instrument named in a sleeve's `exempt` set (a broad
index ETF) does not count as the largest, though its rupees stay in the total. The sleeve-level
limits are in `combine`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.domain.fees import FeeSchedule
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.combine import (
    CombinedBook,
    MonthlyCorrelation,
    SleeveBook,
    SleeveCombiner,
    SleeveRun,
    TransferCost,
)
from emporos.research.swing.costs import ADVERSE, BENCHMARK, CostScenario, SwingCostModel
from emporos.research.swing.data import SwingDataset
from emporos.research.swing.metrics import SwingMetrics, monthly_returns
from emporos.research.swing.rules import Membership, SwingStrategy
from emporos.research.swing.screen import ArmOutcome
from emporos.research.swing.simulator import SwingConfig, SwingRun, SwingSimulator

__all__ = ["BookArm", "BookScreenRun", "SleeveWorld"]


@dataclass(frozen=True)
class SleeveWorld:
    name: str
    dataset: SwingDataset
    strategy: Callable[[], SwingStrategy]  # a fresh instance for each run
    max_positions: int
    cash_yield: Decimal
    benchmark: Callable[[SwingCostModel], SwingRun]  # the sleeve's own same-universe benchmark
    exempt: frozenset[str] = frozenset()  # instruments the single-name concentration check spares
    membership: Membership | None = None  # a name is tradable only while a member (as-of stress)


@dataclass(frozen=True)
class BookArm:
    weights: tuple[Decimal, ...]
    outcome: ArmOutcome  # the book at BENCHMARK costs (and its adverse and benchmark statistics)
    benchmark: SwingRun  # the blended same-weight benchmark
    sleeve_runs: tuple[SleeveRun, ...]  # each sleeve alone, at BENCHMARK costs
    book: CombinedBook  # at BENCHMARK costs
    correlation: MonthlyCorrelation
    strategies: dict[str, SwingStrategy]  # the sleeves' own instances (their records)
    exempt: frozenset[str]

    @property
    def label(self) -> str:
        return "/".join(f"{w * 100:.0f}" for w in self.weights)


class BookScreenRun:
    def __init__(
        self,
        sleeves: Sequence[SleeveWorld],
        schedule: FeeSchedule,
        capital: Decimal,
        start_day: date,
        bootstrap: BlockBootstrap | None = None,
    ) -> None:
        if len(sleeves) < 2:
            raise ValueError("a book has at least two sleeves")
        self._sleeves = tuple(sleeves)
        self._schedule = schedule
        self._capital = capital
        self._start = start_day
        self._bootstrap = bootstrap or BlockBootstrap()

    def run(self, weights: Sequence[Decimal]) -> BookArm:
        exempt = frozenset().union(*(s.exempt for s in self._sleeves))
        base = SwingCostModel(self._schedule, BENCHMARK)
        runs, strategies = self._simulate(weights, BENCHMARK)
        book = self._combine(weights, base, runs)
        adverse_costs = SwingCostModel(self._schedule, ADVERSE)
        adverse_runs, _ = self._simulate(weights, ADVERSE)
        adverse = self._combine(weights, adverse_costs, adverse_runs)
        benchmark = self._combine(
            weights, base,
            [SleeveRun(s.name, s.benchmark(base)) for s in self._sleeves],
        )  # fmt: skip
        outcome = ArmOutcome(
            book.run,
            SleeveBook(dict(strategies)),
            SwingMetrics.of(book.run, exempt),
            SwingMetrics.of(adverse.run, exempt),
            SwingMetrics.of(benchmark.run),
            self._bootstrap.report(SwingMetrics.daily_returns(book.run)),
        )
        first, second = (monthly_returns(r.run) for r in runs[:2])
        return BookArm(
            tuple(weights), outcome, benchmark.run, tuple(runs), book,
            MonthlyCorrelation.of(first, second), strategies, exempt,
        )  # fmt: skip

    def _simulate(
        self, weights: Sequence[Decimal], scenario: CostScenario
    ) -> tuple[list[SleeveRun], dict[str, SwingStrategy]]:
        costs = SwingCostModel(self._schedule, scenario)
        runs: list[SleeveRun] = []
        strategies: dict[str, SwingStrategy] = {}
        for sleeve, weight in zip(self._sleeves, weights, strict=True):
            strategy = sleeve.strategy()
            config = SwingConfig(
                self._capital * weight, sleeve.max_positions, cash_yield=sleeve.cash_yield,
                start_day=self._start,
            )  # fmt: skip
            run = SwingSimulator(sleeve.dataset, strategy, config, costs, sleeve.membership).run()
            runs.append(SleeveRun(sleeve.name, run))
            strategies[sleeve.name] = strategy
        return runs, strategies

    def _combine(
        self, weights: Sequence[Decimal], costs: SwingCostModel, runs: Sequence[SleeveRun]
    ) -> CombinedBook:
        combiner = SleeveCombiner(weights, TransferCost.of(costs), self._capital)
        return combiner.combine(runs)
