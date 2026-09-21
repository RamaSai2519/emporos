"""Running walk-forward: tune on each training window, score each test window ONCE.

    every window x every candidate ─▶ backtest(train) ─▶ objective ─▶ TrainingScore   (one batch)
    for each window:  selector(TrainingScores) ─▶ chosen candidate
                      claim the test window                <- claiming twice raises
    every window's chosen candidate ─▶ backtest(test)      <- exactly once, one batch

The runs of a batch are independent, so a `BatchBacktester` may run them across CPU cores; the
outcome is the same as running them in order. Nothing the selector sees comes from the test window,
and no test run starts before every choice has been made.
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
from emporos.backtest.engine import BacktestResult, BacktestSpec
from emporos.backtest.feed import FeedWindow
from emporos.backtest.metrics.decimal_math import ONE, DecimalMath
from emporos.backtest.tuning import (
    ConfigVariants,
    Objective,
    ParameterCandidate,
    ParameterSelector,
    TrainingScore,
)
from emporos.backtest.walkforward import WalkForwardWindow, WindowSequenceCheck


class ScoredTwiceError(RuntimeError):
    """A test window was about to be scored a second time."""


class ScoredWindows:
    """Which test windows have been scored. A window is claimed, then run; never claimed twice."""

    def __init__(self) -> None:
        self._claimed: set[FeedWindow] = set()

    def claim(self, window: FeedWindow) -> None:
        if window in self._claimed:
            raise ScoredTwiceError(f"the test window {window.start.isoformat()} was already scored")
        self._claimed.add(window)


@dataclass(frozen=True)
class WindowOutcome:
    window: WalkForwardWindow
    training: tuple[TrainingScore, ...]
    chosen: ParameterCandidate
    test: BacktestResult
    test_score: Decimal | None


@dataclass(frozen=True)
class WalkForwardResult:
    objective: str
    outcomes: tuple[WindowOutcome, ...]

    @property
    def compounded_test_return(self) -> Decimal:
        """The windows' out-of-sample returns chained: each test starts from the same cash, so this
        is the growth of one account that was re-tuned before every test window."""
        growth = ONE
        for outcome in self.outcomes:
            growth = DecimalMath.divide(
                growth * outcome.test.metrics.ending_equity.amount,
                outcome.test.metrics.starting_cash.amount,
            )
        return growth - ONE


class WalkForwardRunner:
    def __init__(
        self,
        backtester: Backtester,
        selector: ParameterSelector,
        objective: Objective,
        variants: ConfigVariants,
        check: WindowSequenceCheck | None = None,
        batch: BatchBacktester | None = None,
    ) -> None:
        """`batch` runs the independent backtests (default: `backtester`, one after another)."""
        self._selector = selector
        self._objective = objective
        self._variants = variants
        self._check = check or WindowSequenceCheck()
        self._batch = batch or SerialBatch(backtester)

    async def run(
        self,
        base: BacktestSpec,
        candidates: Sequence[ParameterCandidate],
        windows: Sequence[WalkForwardWindow],
        progress: ProgressSink = ignore_progress,
    ) -> WalkForwardResult:
        names = [c.name for c in candidates]
        if not candidates or len(set(names)) != len(names):
            raise ValueError("walk-forward needs candidates, each named once")
        self._check.verify(windows)
        ledger = ScoredWindows()
        training = await self._train_all(base, candidates, windows, progress)
        chosen = [self._selector.select(scores) for scores in training]  # TrainingScores only
        for window in windows:
            ledger.claim(window.test)
        tests = await self._test_all(base, chosen, windows, progress)
        outcomes = [
            WindowOutcome(window, scores, pick, test, self._objective.score(test))
            for window, scores, pick, test in zip(windows, training, chosen, tests, strict=True)
        ]
        return WalkForwardResult(self._objective.name, tuple(outcomes))

    async def _train_all(
        self,
        base: BacktestSpec,
        candidates: Sequence[ParameterCandidate],
        windows: Sequence[WalkForwardWindow],
        progress: ProgressSink,
    ) -> list[tuple[TrainingScore, ...]]:
        pairs = [(window, candidate) for window in windows for candidate in candidates]
        items = [
            BatchItem(
                f"{base.config.name} w{window.index} train {candidate.name}",
                self._spec(base, candidate, window.train),
            )
            for window, candidate in pairs
        ]
        results = BatchResults(await self._batch.run_many(items, progress)).results()
        scored = [
            TrainingScore(candidate, self._objective.score(result))
            for (_, candidate), result in zip(pairs, results, strict=True)
        ]
        width = len(candidates)
        return [tuple(scored[i : i + width]) for i in range(0, len(scored), width)]

    async def _test_all(
        self,
        base: BacktestSpec,
        chosen: Sequence[ParameterCandidate],
        windows: Sequence[WalkForwardWindow],
        progress: ProgressSink,
    ) -> list[BacktestResult]:
        items = [
            BatchItem(
                f"{base.config.name} w{window.index} test {pick.name}",
                self._spec(base, pick, window.test),
            )
            for window, pick in zip(windows, chosen, strict=True)
        ]
        return BatchResults(await self._batch.run_many(items, progress)).results()

    def _spec(
        self, base: BacktestSpec, candidate: ParameterCandidate, window: FeedWindow
    ) -> BacktestSpec:
        return replace(base, config=self._variants.apply(base.config, candidate), window=window)
