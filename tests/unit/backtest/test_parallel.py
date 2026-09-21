"""EM-131: the process pool returns exactly what the serial runner returns, in the same order,
keeps the results of a batch when one run fails or a worker dies, and reports each completion."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace

import pytest

from emporos.backtest.batch import (
    BatchItem,
    BatchOutcome,
    Completed,
    Failed,
    RunProgress,
    SerialBatch,
)
from emporos.backtest.parallel import ProcessPoolBatch, default_workers
from tests.support.backtest_engine import config, spec
from tests.support.parallel_rig import (
    BROKEN_CASH,
    DEADLY_CASH,
    EngineRecipe,
    MisbehavingBacktester,
)


def batch() -> list[BatchItem]:
    """Runs of very different length, so the workers finish in a different order than submitted."""
    return [
        BatchItem(f"run {n}", spec(config(buy_at=n, sell_at=5), days=days, warmup_bars=0))
        for n, days in ((2, 5), (3, 1), (4, 4), (2, 1), (3, 5), (4, 2))
    ]


def marked(item: BatchItem, cash: object) -> BatchItem:
    return BatchItem(item.label, replace(item.spec, starting_cash=cash))  # type: ignore[type-var]


@pytest.fixture
def pool() -> Iterator[ProcessPoolBatch]:
    with ProcessPoolBatch(EngineRecipe(), workers=3) as opened:
        yield opened


def completed(outcomes: Sequence[BatchOutcome]) -> list[Completed]:
    assert all(isinstance(o, Completed) for o in outcomes)
    return [o for o in outcomes if isinstance(o, Completed)]


async def test_the_pool_returns_exactly_what_the_serial_runner_returns(
    pool: ProcessPoolBatch,
) -> None:
    serial = completed(await SerialBatch(MisbehavingBacktester()).run_many(batch()))

    parallel = completed(await pool.run_many(batch()))

    assert [o.label for o in parallel] == [o.label for o in serial]
    assert [o.result.run_id for o in parallel] == [o.result.run_id for o in serial]
    assert [o.result.metrics for o in parallel] == [o.result.metrics for o in serial]
    assert [o.result.trades for o in parallel] == [o.result.trades for o in serial]
    assert [o.result.config_hash for o in parallel] == [o.result.config_hash for o in serial]


async def test_the_order_is_the_order_given_whatever_finished_first(pool: ProcessPoolBatch) -> None:
    seen: list[RunProgress] = []

    outcomes = await pool.run_many(batch(), seen.append)

    assert [o.label for o in outcomes] == [i.label for i in batch()]
    assert sorted(e.done for e in seen) == [1, 2, 3, 4, 5, 6]  # one event per completed run
    assert all(e.total == 6 for e in seen)


async def test_a_failed_run_is_reported_and_the_others_survive(pool: ProcessPoolBatch) -> None:
    items = batch()
    items[2] = marked(items[2], BROKEN_CASH)

    outcomes = await pool.run_many(items)

    failure = outcomes[2]
    assert isinstance(failure, Failed)
    assert failure.label == "run 4"
    assert failure.window == items[2].spec.window
    assert "ValueError: the marked run failed" in failure.error
    assert [type(o) for i, o in enumerate(outcomes) if i != 2] == [Completed] * 5


async def test_a_worker_that_dies_fails_its_batch_but_the_pool_recovers(
    pool: ProcessPoolBatch,
) -> None:
    items = batch()
    items[0] = marked(items[0], DEADLY_CASH)

    outcomes = await pool.run_many(items)

    first = outcomes[0]
    assert isinstance(first, Failed)
    assert "worker died" in first.error
    later = await pool.run_many(batch()[:2])  # a fresh pool is started for the next batch
    assert [type(o) for o in later] == [Completed, Completed]


async def test_an_empty_batch_starts_no_workers(pool: ProcessPoolBatch) -> None:
    assert await pool.run_many([]) == []


def test_a_pool_needs_at_least_one_worker() -> None:
    with pytest.raises(ValueError, match="at least one worker"):
        ProcessPoolBatch(EngineRecipe(), workers=0)


GIB = 1024**3


def test_the_default_leaves_a_core_free() -> None:
    assert default_workers(available=64 * GIB, cores=8) == 7
    assert default_workers(cores=1) == 1


def test_the_default_is_held_back_by_memory_not_only_by_cores() -> None:
    assert default_workers(available=5 * GIB, cores=8) == 4  # (5 - 2 GiB reserve) / 768 MiB
    assert default_workers(available=1 * GIB, cores=8) == 1  # never fewer than one


def test_an_unknown_memory_falls_back_to_the_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("emporos.backtest.parallel.available_memory", lambda: None)

    assert default_workers(cores=8) == 7
