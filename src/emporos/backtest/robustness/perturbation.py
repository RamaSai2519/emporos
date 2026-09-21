"""Does the result survive a nudge to the parameters, or does it live on one lucky setting?

For each walk-forward window, the parameter set that was chosen (on training data alone) is
compared with its NEIGHBOURS: the other candidates of the pre-declared grid, or, where the grid
varies one parameter at a time, just those that differ from the chosen one in exactly one
parameter. Each neighbour is backtested on the same test window and the share that still make
money is the stability figure. This is a diagnostic read of the test window: nothing here chooses
a parameter, and its result cannot feed back into selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from emporos.backtest.batch import (
    Backtester,
    BatchBacktester,
    BatchItem,
    BatchResults,
    ProgressSink,
    SerialBatch,
    ignore_progress,
)
from emporos.backtest.engine import BacktestSpec
from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.tuning import ConfigVariants, ParameterCandidate
from emporos.backtest.walkforward_run import WindowOutcome


class NeighbourSelector:
    def of(
        self, chosen: ParameterCandidate, candidates: Sequence[ParameterCandidate]
    ) -> tuple[ParameterCandidate, ...]:
        others = [c for c in candidates if c.name != chosen.name]
        single = [c for c in others if self._parameters_apart(chosen, c) == 1]
        return tuple(single or others)

    @staticmethod
    def _parameters_apart(a: ParameterCandidate, b: ParameterCandidate) -> int:
        keys = set(a.overrides) | set(b.overrides)
        return sum(1 for key in keys if a.overrides.get(key) != b.overrides.get(key))


@dataclass(frozen=True)
class NeighbourRun:
    window: int
    candidate: str
    net_pnl: Decimal


@dataclass(frozen=True)
class PerturbationReport:
    runs: tuple[NeighbourRun, ...]

    @property
    def profitable_share(self) -> Decimal | None:
        if not self.runs:
            return None
        return DecimalMath.divide(
            Decimal(sum(1 for r in self.runs if r.net_pnl > ZERO)), Decimal(len(self.runs))
        )


class PerturbationRunner:
    def __init__(
        self,
        backtester: Backtester,
        variants: ConfigVariants | None = None,
        neighbours: NeighbourSelector | None = None,
        batch: BatchBacktester | None = None,
    ) -> None:
        self._batch = batch or SerialBatch(backtester)
        self._variants = variants or ConfigVariants()
        self._neighbours = neighbours or NeighbourSelector()

    async def evaluate(
        self,
        base: BacktestSpec,
        outcomes: Sequence[WindowOutcome],
        candidates: Sequence[ParameterCandidate],
        progress: ProgressSink = ignore_progress,
    ) -> PerturbationReport:
        wanted = [
            (index, neighbour)
            for index, outcome in enumerate(outcomes)
            for neighbour in self._neighbours.of(outcome.chosen, candidates)
        ]
        items = [
            BatchItem(
                f"{base.config.name} w{index} neighbour {neighbour.name}",
                replace(
                    base,
                    config=self._variants.apply(base.config, neighbour),
                    window=outcomes[index].window.test,
                ),
            )
            for index, neighbour in wanted
        ]
        results = BatchResults(await self._batch.run_many(items, progress)).results()
        return PerturbationReport(
            tuple(
                NeighbourRun(index, neighbour.name, result.metrics.trades.net_pnl.amount)
                for (index, neighbour), result in zip(wanted, results, strict=True)
            )
        )
