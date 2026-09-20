"""Running walk-forward: tune on each training window, score each test window ONCE.

    for each window:  every candidate ─▶ backtest(train) ─▶ objective ─▶ TrainingScore
                      selector(TrainingScores) ─▶ chosen candidate
                      chosen ─▶ backtest(test)          <- exactly once, recorded in the ledger

The test window is claimed before it is run; claiming twice raises. Nothing the selector sees comes
from the test window, and the test run happens only after the choice is made.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Protocol

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


class Backtester(Protocol):
    async def run(self, spec: BacktestSpec) -> BacktestResult: ...


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
    ) -> None:
        self._backtester = backtester
        self._selector = selector
        self._objective = objective
        self._variants = variants
        self._check = check or WindowSequenceCheck()

    async def run(
        self,
        base: BacktestSpec,
        candidates: Sequence[ParameterCandidate],
        windows: Sequence[WalkForwardWindow],
    ) -> WalkForwardResult:
        names = [c.name for c in candidates]
        if not candidates or len(set(names)) != len(names):
            raise ValueError("walk-forward needs candidates, each named once")
        self._check.verify(windows)
        ledger = ScoredWindows()
        outcomes = [await self._one(base, candidates, window, ledger) for window in windows]
        return WalkForwardResult(self._objective.name, tuple(outcomes))

    async def _one(
        self,
        base: BacktestSpec,
        candidates: Sequence[ParameterCandidate],
        window: WalkForwardWindow,
        ledger: ScoredWindows,
    ) -> WindowOutcome:
        training = tuple([await self._train(base, candidate, window) for candidate in candidates])
        chosen = self._selector.select(training)  # sees TrainingScores only
        ledger.claim(window.test)
        test = await self._backtester.run(self._spec(base, chosen, window.test))
        return WindowOutcome(window, training, chosen, test, self._objective.score(test))

    async def _train(
        self, base: BacktestSpec, candidate: ParameterCandidate, window: WalkForwardWindow
    ) -> TrainingScore:
        result = await self._backtester.run(self._spec(base, candidate, window.train))
        return TrainingScore(candidate, self._objective.score(result))

    def _spec(
        self, base: BacktestSpec, candidate: ParameterCandidate, window: FeedWindow
    ) -> BacktestSpec:
        return replace(base, config=self._variants.apply(base.config, candidate), window=window)
