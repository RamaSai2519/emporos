"""Running a batch of backtests across CPU cores.

    parent process                              worker processes (spawned, no shared state)
    ──────────────                              ───────────────────────────────────────────
    PrefetchingBatch: put every month the       recipe.build() once per process ─▶ a Backtester
      batch reads on disk (cache / snapshot)    each item: its own run, on the files only
    ProcessPoolBatch: send each spec to a
      worker, collect results BY POSITION       result (or the error text) back to the parent

A worker is rebuilt from a `BacktesterRecipe`, a small picklable description (paths, settings), so
nothing live (a database connection, an event loop) crosses the process boundary; its candle reader
is file-only, so it cannot reach Atlas. Every run is its own engine call on its own state, so a
parallel batch returns exactly what `SerialBatch` returns: the same run ids and metrics, in the
same order, whichever worker finished first.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from emporos.backtest.batch import (
    Backtester,
    BatchBacktester,
    BatchItem,
    BatchOutcome,
    Completed,
    Failed,
    ProgressSink,
    RunProgress,
    ignore_progress,
)
from emporos.backtest.engine import BacktestResult, BacktestSpec
from emporos.persistence.candle_cache import CandleNeed


class BacktesterRecipe(Protocol):
    """A picklable description of how to build a backtester inside a worker process."""

    def build(self) -> Backtester: ...


@dataclass(frozen=True)
class _WorkerFailure:
    error: str


_backtester: Backtester | None = None  # this worker process's engine, made once by `_initialise`


def _initialise(recipe: BacktesterRecipe) -> None:
    global _backtester
    _backtester = recipe.build()


def _execute(spec: BacktestSpec) -> BacktestResult | _WorkerFailure:
    """Runs in a worker. Returns a failure as a value: an exception that cannot itself be pickled
    would otherwise take the whole pool down."""
    assert _backtester is not None, "the worker was not initialised"
    try:
        return asyncio.run(_backtester.run(spec))
    except Exception as error:
        return _WorkerFailure(f"{type(error).__name__}: {error}")


WORKER_MEMORY = 768 * 1024 * 1024  # what one worker holds: the engine, its bars and a run's state
MEMORY_RESERVE = 2 * 1024 * 1024 * 1024  # left for everything else on the machine


def available_memory() -> int | None:
    """Bytes the machine can give a new process without swapping (Linux), or None if unknown."""
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def default_workers(available: int | None = None, cores: int | None = None) -> int:
    """One fewer than the cores, so the machine stays usable, and no more than memory allows: a
    machine that swaps runs slower than one that uses fewer workers (and can fall over)."""
    by_cpu = max(1, (cores or os.cpu_count() or 2) - 1)
    free = available_memory() if available is None else available
    if free is None:
        return by_cpu
    return max(1, min(by_cpu, (free - MEMORY_RESERVE) // WORKER_MEMORY))


class ProcessPoolBatch:
    """Runs each item of a batch in a pool of worker processes."""

    def __init__(self, recipe: BacktesterRecipe, workers: int | None = None) -> None:
        workers = default_workers() if workers is None else workers
        if workers < 1:
            raise ValueError("a process pool needs at least one worker")
        self._recipe = recipe
        self._workers = workers
        self._pool: ProcessPoolExecutor | None = None

    @property
    def workers(self) -> int:
        return self._workers

    def close(self) -> None:
        """Stop the workers, abandoning anything still queued."""
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> ProcessPoolBatch:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    async def run_many(
        self, items: Sequence[BatchItem], progress: ProgressSink = ignore_progress
    ) -> list[BatchOutcome]:
        if not items:
            return []
        loop = asyncio.get_running_loop()
        pool = self._ensure_pool()
        outcomes: list[BatchOutcome | None] = [None] * len(items)
        done = 0

        async def one(index: int) -> None:
            nonlocal done
            item = items[index]
            try:
                returned = await loop.run_in_executor(pool, _execute, item.spec)
                outcomes[index] = self._outcome(item, returned)
            except BrokenProcessPool as error:
                self._pool = None  # unusable; the next batch starts a fresh one
                outcomes[index] = Failed(item.label, item.spec.window, f"worker died: {error}")
            done += 1
            outcome = outcomes[index]
            assert outcome is not None
            progress(RunProgress(done, len(items), outcome))

        try:
            await asyncio.gather(*(one(i) for i in range(len(items))))
        except BaseException:
            self.close()  # cancelled or interrupted: do not leave workers running the queue
            raise
        return [o for o in outcomes if o is not None]

    def _ensure_pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                self._workers,
                mp_context=multiprocessing.get_context("spawn"),  # never fork a live DB client
                initializer=_initialise,
                initargs=(self._recipe,),
            )
        return self._pool

    @staticmethod
    def _outcome(item: BatchItem, returned: BacktestResult | _WorkerFailure) -> BatchOutcome:
        if isinstance(returned, _WorkerFailure):
            return Failed(item.label, item.spec.window, returned.error)
        return Completed(item.label, returned)


class CandlePreparer(Protocol):
    async def ensure(self, needs: Iterable[CandleNeed]) -> None: ...


class PrefetchingBatch:
    """Before each batch, makes sure every bar its runs will read is on disk for the workers."""

    def __init__(
        self,
        inner: BatchBacktester,
        preparer: CandlePreparer,
        needs_of: Callable[[BacktestSpec], Iterable[CandleNeed]],
    ) -> None:
        self._inner = inner
        self._preparer = preparer
        self._needs_of = needs_of

    async def run_many(
        self, items: Sequence[BatchItem], progress: ProgressSink = ignore_progress
    ) -> list[BatchOutcome]:
        await self._preparer.ensure(need for item in items for need in self._needs_of(item.spec))
        return await self._inner.run_many(items, progress)


class SpecNeeds:
    """The bars a spec's run reads: its window, plus the warm-up before it, for every instrument."""

    def __call__(self, spec: BacktestSpec) -> Iterable[CandleNeed]:
        config = spec.config
        start = spec.window.start - (spec.warmup_lookback if spec.warmup_bars else timedelta(0))
        return [
            CandleNeed(instrument_id, config.timeframe, start, spec.window.end)
            for instrument_id in config.instrument_ids
        ]
