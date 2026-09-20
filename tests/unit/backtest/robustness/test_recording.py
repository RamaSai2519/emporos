"""Every backtest a walk-forward run performs becomes a trial: the candidates that lost included."""

from datetime import UTC, datetime
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.backtest.robustness.recording import (
    LedgerRecorder,
    NoRecording,
    TrialContext,
    WalkForwardTrials,
)
from emporos.backtest.robustness.trials import InMemoryTrialLedger
from emporos.backtest.tuning import NET_PNL, SHARPE
from emporos.backtest.walkforward_run import WalkForwardResult
from emporos.domain.experiments import TrialRole
from tests.unit.backtest.test_walkforward_run import (
    CANDIDATES,
    RecordingBacktester,
    base_spec,
    runner,
    windows,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def context(batch: str = "b1") -> TrialContext:
    return TrialContext("exp", batch, "data-v1", "fees-v1", NOW)


async def walk(objective=SHARPE) -> WalkForwardResult:  # type: ignore[no-untyped-def]
    return await runner(RecordingBacktester(), objective=objective).run(
        base_spec(), CANDIDATES, windows()
    )


async def test_every_candidate_on_every_window_is_a_trial_plus_each_test_run() -> None:
    result = await walk()

    trials = WalkForwardTrials(context()).of("strat", result)

    windows_run = len(result.outcomes)
    assert len(trials) == windows_run * (len(CANDIDATES) + 1)
    assert len({t.trial_id for t in trials}) == len(trials)
    roles = [t.role for t in trials]
    assert roles.count(TrialRole.TRAIN) == windows_run * len(CANDIDATES)
    assert roles.count(TrialRole.TEST) == windows_run


async def test_a_test_trial_carries_the_run_it_came_from() -> None:
    result = await walk()

    trials = WalkForwardTrials(context()).of("strat", result)

    tests = [t for t in trials if t.role is TrialRole.TEST]
    for trial, outcome in zip(tests, result.outcomes, strict=True):
        assert trial.run_id == outcome.test.run_id
        assert trial.config_hash == outcome.test.config_hash
        assert trial.candidate == outcome.chosen.name
        assert trial.net_pnl == outcome.test.metrics.trades.net_pnl.amount
        assert trial.trade_count == outcome.test.metrics.trades.count


async def test_a_sharpe_search_keeps_each_training_score_per_day() -> None:
    result = await walk(SHARPE)

    trials = WalkForwardTrials(context()).of("strat", result)

    scored = [
        (score.score, t)
        for outcome, chunk in zip(
            result.outcomes,
            [
                trials[i : i + len(CANDIDATES) + 1]
                for i in range(0, len(trials), len(CANDIDATES) + 1)
            ],
            strict=True,
        )
        for score, t in zip(outcome.training, chunk[:-1], strict=True)
    ]
    root = DecimalMath.sqrt(Decimal(252))
    for score, trial in scored:
        expected = None if score is None else DecimalMath.divide(score, root)
        assert trial.daily_sharpe == expected


async def test_a_search_on_another_objective_records_no_sharpe_it_cannot_know() -> None:
    result = await walk(NET_PNL)

    trials = WalkForwardTrials(context()).of("strat", result)

    assert all(t.daily_sharpe is None for t in trials if t.role is TrialRole.TRAIN)


async def test_the_recorder_appends_all_and_a_repeat_under_a_new_batch_adds_more() -> None:
    result = await walk()
    ledger = InMemoryTrialLedger()

    await LedgerRecorder(ledger, WalkForwardTrials(context("b1"))).record("strat", result)
    first = len(await ledger.all())
    await LedgerRecorder(ledger, WalkForwardTrials(context("b2"))).record("strat", result)

    assert first > 0
    assert len(await ledger.all()) == 2 * first


async def test_no_recording_records_nothing() -> None:
    assert await NoRecording().record("strat", await walk()) is None
