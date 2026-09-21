"""EM-131: walk-forward, neighbour and baseline runs give the same answer whether their backtests
run one after another or in a process pool, and a failed run names its window and candidate."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta

import pytest

from emporos.backtest.batch import BatchFailedError, RunProgress, SerialBatch
from emporos.backtest.parallel import ProcessPoolBatch
from emporos.backtest.robustness.assessment import HoldBaseline
from emporos.backtest.robustness.perturbation import PerturbationRunner
from emporos.backtest.tuning import NET_PNL, BestScoreSelector, ConfigVariants, ParameterCandidate
from emporos.backtest.walkforward import WalkForwardMode, WalkForwardPlanner, WalkForwardWindow
from emporos.backtest.walkforward_run import WalkForwardRunner
from tests.support.backtest_engine import config, spec
from tests.support.parallel_rig import BROKEN_CASH, SESSIONS, EngineRecipe, MisbehavingBacktester
from tests.support.strategies import T0

DAY = timedelta(days=1)
START = T0 - timedelta(hours=1)
CANDIDATES = (
    ParameterCandidate("early", {"buy_at": 2, "sell_at": 5}),
    ParameterCandidate("later", {"buy_at": 3, "sell_at": 5}),
    ParameterCandidate("held", {"buy_at": 2, "sell_at": 7}),
)


def windows() -> tuple[WalkForwardWindow, ...]:
    return WalkForwardPlanner().plan(
        WalkForwardMode.ROLLING, START, START + SESSIONS * DAY, 3 * DAY, DAY
    )


@pytest.fixture
def pool() -> Iterator[ProcessPoolBatch]:
    with ProcessPoolBatch(EngineRecipe(), workers=3) as opened:
        yield opened


def runner(batch) -> WalkForwardRunner:  # type: ignore[no-untyped-def]
    return WalkForwardRunner(
        MisbehavingBacktester(), BestScoreSelector(), NET_PNL, ConfigVariants(), batch=batch
    )


def base():  # type: ignore[no-untyped-def]
    return spec(config(buy_at=2, sell_at=5), days=SESSIONS)


async def test_a_parallel_walk_forward_equals_the_serial_one(pool: ProcessPoolBatch) -> None:
    serial = await runner(SerialBatch(MisbehavingBacktester())).run(base(), CANDIDATES, windows())

    parallel = await runner(pool).run(base(), CANDIDATES, windows())

    assert parallel == serial
    assert len(parallel.outcomes) == len(windows())


async def test_progress_counts_every_training_and_test_run(pool: ProcessPoolBatch) -> None:
    seen: list[RunProgress] = []

    await runner(pool).run(base(), CANDIDATES, windows(), seen.append)

    plan = windows()
    assert len(seen) == len(plan) * len(CANDIDATES) + len(plan)
    labels = {e.outcome.label for e in seen}
    assert "buy_then_sell w0 train early" in labels
    assert sum(1 for label in labels if label.startswith("buy_then_sell w0 test ")) == 1


async def test_a_failed_run_is_named_by_window_and_candidate_and_the_rest_are_kept(
    pool: ProcessPoolBatch,
) -> None:
    broken = replace(base(), starting_cash=BROKEN_CASH)

    with pytest.raises(BatchFailedError) as raised:
        await runner(pool).run(broken, CANDIDATES, windows())

    names = [f.label for f in raised.value.failures]
    assert "buy_then_sell w0 train early" in names and "buy_then_sell w2 train held" in names
    assert all(f.window.start < f.window.end for f in raised.value.failures)
    assert raised.value.completed == ()


async def test_neighbours_and_baseline_agree_between_serial_and_parallel(
    pool: ProcessPoolBatch,
) -> None:
    result = await runner(SerialBatch(MisbehavingBacktester())).run(base(), CANDIDATES, windows())
    serial = SerialBatch(MisbehavingBacktester())

    a = await PerturbationRunner(MisbehavingBacktester(), batch=serial).evaluate(
        base(), result.outcomes, CANDIDATES
    )
    b = await PerturbationRunner(MisbehavingBacktester(), batch=pool).evaluate(
        base(), result.outcomes, CANDIDATES
    )
    hold = config(buy_at=3, sell_at=0)
    c = await HoldBaseline(MisbehavingBacktester(), hold, serial).net_pnl(base(), result)
    d = await HoldBaseline(MisbehavingBacktester(), hold, pool).net_pnl(base(), result)

    assert a == b and a.runs
    assert c == d
