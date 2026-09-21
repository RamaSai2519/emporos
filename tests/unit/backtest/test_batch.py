"""EM-131: a batch returns one outcome per item in the order given, keeps what completed when a run
fails, and reports every completion."""

from __future__ import annotations

from dataclasses import replace

import pytest

from emporos.backtest.batch import (
    BatchFailedError,
    BatchItem,
    BatchResults,
    Completed,
    Failed,
    RunProgress,
    SerialBatch,
)
from tests.support.backtest_engine import config, spec
from tests.support.parallel_rig import BROKEN_CASH, MisbehavingBacktester


def items() -> list[BatchItem]:
    return [
        BatchItem(f"run {n}", spec(config(buy_at=n, sell_at=5), days=2, warmup_bars=0))
        for n in (2, 3, 4)
    ]


def broken(label: str) -> BatchItem:
    good = items()[0]
    return BatchItem(label, replace(good.spec, starting_cash=BROKEN_CASH))


class Recorder:
    def __init__(self) -> None:
        self.events: list[RunProgress] = []

    def __call__(self, progress: RunProgress) -> None:
        self.events.append(progress)


async def test_outcomes_come_back_in_the_order_given() -> None:
    outcomes = await SerialBatch(MisbehavingBacktester()).run_many(items())

    assert [o.label for o in outcomes] == ["run 2", "run 3", "run 4"]
    assert all(isinstance(o, Completed) for o in outcomes)


async def test_a_failed_run_is_reported_with_its_label_and_costs_the_others_nothing() -> None:
    batch = items()
    batch[1] = broken("run broken")

    outcomes = await SerialBatch(MisbehavingBacktester()).run_many(batch)

    assert [type(o) for o in outcomes] == [Completed, Failed, Completed]
    failure = outcomes[1]
    assert isinstance(failure, Failed)
    assert failure.label == "run broken"
    assert failure.window == batch[1].spec.window
    assert "ValueError: the marked run failed" in failure.error
    assert "run broken" in failure.describe()


async def test_every_completion_is_reported_with_a_running_count() -> None:
    seen = Recorder()

    await SerialBatch(MisbehavingBacktester()).run_many(items(), seen)

    assert [(e.done, e.total) for e in seen.events] == [(1, 3), (2, 3), (3, 3)]
    assert [e.outcome.label for e in seen.events] == ["run 2", "run 3", "run 4"]


async def test_results_are_handed_over_only_when_every_run_completed() -> None:
    good = await SerialBatch(MisbehavingBacktester()).run_many(items())
    assert len(BatchResults(good).results()) == 3


async def test_a_failure_raises_but_keeps_the_completed_results() -> None:
    outcomes = await SerialBatch(MisbehavingBacktester()).run_many(
        [items()[0], broken("bad"), items()[2]]
    )

    with pytest.raises(BatchFailedError) as raised:
        BatchResults(outcomes).results()

    assert [f.label for f in raised.value.failures] == ["bad"]
    assert [c.label for c in raised.value.completed] == ["run 2", "run 4"]
    assert "1 of 3 backtest(s) failed" in str(raised.value)


async def test_an_empty_batch_is_an_empty_result() -> None:
    assert await SerialBatch(MisbehavingBacktester()).run_many([]) == []
