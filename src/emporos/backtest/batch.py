"""Running many independent backtests as one batch.

A curation is dozens of backtests that share nothing: each is its own spec (config, window) run on
its own engine. `BatchBacktester` is the one-method interface for "run these and tell me how each
went"; how they are run (one after another, or across CPU cores) is the implementation's business,
and the result is the same either way:

* one outcome per item, IN THE ORDER GIVEN, whatever order the runs finished in;
* a run that raised is reported as `Failed`, with its label and window, and never costs the others:
  every result that did complete is returned.

`SerialBatch` is the reference implementation; the process pool (`emporos.backtest.parallel`) must
return exactly what it returns.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from emporos.backtest.engine import BacktestResult, BacktestSpec
from emporos.backtest.feed import FeedWindow


class Backtester(Protocol):
    async def run(self, spec: BacktestSpec) -> BacktestResult: ...


@dataclass(frozen=True)
class BatchItem:
    """One backtest to run. The label says which one it is in a report ("orb_v1 w2 train fast")."""

    label: str
    spec: BacktestSpec


@dataclass(frozen=True)
class Completed:
    label: str
    result: BacktestResult

    @property
    def failed(self) -> bool:
        return False


@dataclass(frozen=True)
class Failed:
    label: str
    window: FeedWindow
    error: str  # "ExceptionType: message"

    @property
    def failed(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.label} [{self.window.start.date()}..{self.window.end.date()}]: {self.error}"


BatchOutcome = Completed | Failed


@dataclass(frozen=True)
class RunProgress:
    """One backtest finished: how many of the batch are done, and how it went."""

    done: int
    total: int
    outcome: BatchOutcome


ProgressSink = Callable[[RunProgress], None]


def ignore_progress(_progress: RunProgress) -> None:
    return None


class BatchBacktester(Protocol):
    async def run_many(
        self, items: Sequence[BatchItem], progress: ProgressSink = ignore_progress
    ) -> list[BatchOutcome]:
        """One outcome per item, in the order of `items`. Never raises for a failed run."""
        ...


class BatchFailedError(RuntimeError):
    """Some runs of a batch failed. The results that did complete are kept in `completed`."""

    def __init__(self, failures: Sequence[Failed], completed: Sequence[Completed]) -> None:
        self.failures = tuple(failures)
        self.completed = tuple(completed)
        shown = "; ".join(f.describe() for f in self.failures[:5])
        more = "" if len(self.failures) <= 5 else f" (+{len(self.failures) - 5} more)"
        super().__init__(
            f"{len(self.failures)} of {len(self.failures) + len(self.completed)} backtest(s) "
            f"failed: {shown}{more}"
        )


class BatchResults:
    """The outcomes of a batch, for a caller that needs every run to have completed."""

    def __init__(self, outcomes: Sequence[BatchOutcome]) -> None:
        self._outcomes = tuple(outcomes)

    def results(self) -> list[BacktestResult]:
        """Every result in order, or `BatchFailedError` (carrying the completed ones)."""
        failures = [o for o in self._outcomes if isinstance(o, Failed)]
        completed = [o for o in self._outcomes if isinstance(o, Completed)]
        if failures:
            raise BatchFailedError(failures, completed)
        return [o.result for o in completed]


class SerialBatch:
    """Runs a batch one item after another on a single backtester."""

    def __init__(self, backtester: Backtester) -> None:
        self._backtester = backtester

    async def run_many(
        self, items: Sequence[BatchItem], progress: ProgressSink = ignore_progress
    ) -> list[BatchOutcome]:
        outcomes: list[BatchOutcome] = []
        for item in items:
            outcomes.append(await self._one(item))
            progress(RunProgress(len(outcomes), len(items), outcomes[-1]))
        return outcomes

    async def _one(self, item: BatchItem) -> BatchOutcome:
        try:
            return Completed(item.label, await self._backtester.run(item.spec))
        except Exception as error:
            return Failed(item.label, item.spec.window, f"{type(error).__name__}: {error}")
