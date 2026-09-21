"""EM-107: walk-forward runs on the real engine: selection sees only training, tests scored once."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.backtest.engine import BacktestResult, BacktestSpec
from emporos.backtest.feed import FeedWindow
from emporos.backtest.tuning import (
    NET_PNL,
    SHARPE,
    TOTAL_RETURN,
    BestScoreSelector,
    ConfigVariants,
    MetricObjective,
    NoViableCandidateError,
    ParameterCandidate,
    ParameterError,
    TrainingScore,
)
from emporos.backtest.walkforward import (
    LeakageError,
    WalkForwardMode,
    WalkForwardPlanner,
    WalkForwardWindow,
)
from emporos.backtest.walkforward_run import ScoredTwiceError, ScoredWindows, WalkForwardRunner
from tests.support.backtest_engine import DOWN_DAY, WORKED_DAY, bars, config, engine, spec
from tests.support.strategies import T0

DAY = timedelta(days=1)
START = T0 - timedelta(hours=1)  # an hour before the first session opens
SESSIONS = 8
CANDIDATES = (
    ParameterCandidate("early", {"buy_at": 2, "sell_at": 5}),
    ParameterCandidate("later", {"buy_at": 3, "sell_at": 5}),
    ParameterCandidate("held", {"buy_at": 2, "sell_at": 7}),
)


def candles():  # type: ignore[no-untyped-def]
    """Sessions 0-3 rise (WORKED_DAY), sessions 4-7 fall (DOWN_DAY): what wins in one is not what
    wins in the other, so a selection that peeked at the test window would choose differently."""
    return [
        bar for day in range(SESSIONS) for bar in bars(WORKED_DAY if day < 4 else DOWN_DAY, day=day)
    ]


class RecordingBacktester:
    """The real engine, remembering every window it was asked to run and with which parameters."""

    def __init__(self) -> None:
        self._engine = engine(candles())
        self.runs: list[tuple[FeedWindow, int, int]] = []

    async def run(self, request: BacktestSpec) -> BacktestResult:
        params = request.config.parameters
        self.runs.append((request.window, params.buy_at, params.sell_at))  # type: ignore[attr-defined]
        return await self._engine.run(request)


class SpySelector:
    """A `ParameterSelector` that records exactly what it was handed."""

    def __init__(self) -> None:
        self.received: list[tuple[TrainingScore, ...]] = []
        self._inner = BestScoreSelector()

    def select(self, scores):  # type: ignore[no-untyped-def]
        self.received.append(tuple(scores))
        return self._inner.select(scores)


def windows(mode: WalkForwardMode = WalkForwardMode.ROLLING) -> tuple[WalkForwardWindow, ...]:
    return WalkForwardPlanner().plan(mode, START, START + SESSIONS * DAY, 3 * DAY, DAY)


def runner(backtester: RecordingBacktester, selector: SpySelector | None = None, objective=NET_PNL):  # type: ignore[no-untyped-def]
    return WalkForwardRunner(backtester, selector or SpySelector(), objective, ConfigVariants())


def base_spec() -> BacktestSpec:
    return spec(config(buy_at=2, sell_at=5), days=SESSIONS)


async def independent_score(candidate: ParameterCandidate, window: FeedWindow) -> Decimal | None:
    """What a candidate scores on a window, worked out WITHOUT the walk-forward runner."""
    fresh = engine(candles())
    varied = ConfigVariants().apply(base_spec().config, candidate)
    result = await fresh.run(BacktestSpec(varied, window, base_spec().starting_cash, warmup_bars=0))
    return NET_PNL.score(result)


class TestSelectionSeesOnlyTraining:
    async def test_every_training_run_is_on_the_training_window_and_the_test_run_comes_after(
        self,
    ) -> None:
        backtester = RecordingBacktester()
        plan = windows()

        result = await runner(backtester).run(base_spec(), CANDIDATES, plan)

        assert len(result.outcomes) == len(plan) == 5
        trained = len(plan) * len(CANDIDATES)
        training, testing = backtester.runs[:trained], backtester.runs[trained:]
        # every candidate is tried on every training window before ANY test window is run ...
        for index, window in enumerate(plan):
            block = training[index * len(CANDIDATES) : (index + 1) * len(CANDIDATES)]
            assert [w for w, _, _ in block] == [window.train] * len(CANDIDATES)
            assert [(b, s) for _, b, s in block] == [
                (c.overrides["buy_at"], c.overrides["sell_at"]) for c in CANDIDATES
            ]
        # ... and then each test window is run once, in order
        assert [w for w, _, _ in testing] == [window.test for window in plan]

    async def test_no_training_run_touches_any_bar_of_its_own_test_window(self) -> None:
        backtester = RecordingBacktester()
        plan = windows()
        await runner(backtester).run(base_spec(), CANDIDATES, plan)

        train_windows = {w.train for w in plan}
        for window, _, _ in backtester.runs:
            if window in train_windows:
                owner = next(p for p in plan if p.train == window)
                assert window.end <= owner.test.start

    async def test_the_selector_is_handed_training_scores_and_nothing_else(self) -> None:
        backtester, selector = RecordingBacktester(), SpySelector()
        plan = windows()

        await runner(backtester, selector).run(base_spec(), CANDIDATES, plan)

        assert len(selector.received) == len(plan)
        for scores, window in zip(selector.received, plan, strict=True):
            assert all(type(s) is TrainingScore for s in scores)
            assert [s.candidate for s in scores] == list(CANDIDATES)
            for s in scores:
                assert s.score == await independent_score(s.candidate, window.train)

    async def test_the_chosen_candidate_is_the_best_on_training_not_on_test(self) -> None:
        backtester = RecordingBacktester()
        plan = windows()

        result = await runner(backtester).run(base_spec(), CANDIDATES, plan)

        for outcome in result.outcomes:
            train_scores = {
                c.name: await independent_score(c, outcome.window.train) for c in CANDIDATES
            }
            best = max(train_scores.values())
            assert train_scores[outcome.chosen.name] == best
            assert outcome.test_score == await independent_score(
                outcome.chosen, outcome.window.test
            )

    async def test_the_selection_would_differ_if_the_test_window_had_been_used(self) -> None:
        """Control for the test above. Window 1 trains on a rising day and tests on a falling one:
        the best candidate on TEST is not the best on TRAIN, so 'chose the training winner' is
        not true by accident, and a selector that peeked would have chosen differently."""
        plan = windows()
        result = await runner(RecordingBacktester()).run(base_spec(), CANDIDATES, plan)
        window = plan[1]
        train = {c.name: await independent_score(c, window.train) for c in CANDIDATES}
        test = {c.name: await independent_score(c, window.test) for c in CANDIDATES}

        best_on_train = max(train, key=lambda name: train[name])  # type: ignore[arg-type,return-value]
        best_on_test = max(test, key=lambda name: test[name])  # type: ignore[arg-type,return-value]
        assert best_on_train != best_on_test
        assert result.outcomes[1].chosen.name == best_on_train


class TestTestWindowsAreScoredOnce:
    async def test_each_test_window_is_run_exactly_once(self) -> None:
        backtester = RecordingBacktester()
        plan = windows()
        await runner(backtester).run(base_spec(), CANDIDATES, plan)

        test_runs = [w for w, _, _ in backtester.runs if w in {p.test for p in plan}]
        assert sorted(w.start for w in test_runs) == sorted(p.test.start for p in plan)
        assert len(test_runs) == len(plan)

    async def test_the_runner_itself_refuses_a_second_scoring_even_with_the_sequence_check_off(
        self,
    ) -> None:
        """Defence in depth: the ledger is a second guard behind `WindowSequenceCheck`."""

        class Permissive:
            def verify(self, windows: object) -> None:
                return None

        backtester = RecordingBacktester()
        plan = windows()
        twice = [plan[0], plan[0]]
        lenient = WalkForwardRunner(
            backtester,
            SpySelector(),
            NET_PNL,
            ConfigVariants(),
            check=Permissive(),  # type: ignore[arg-type]
        )

        with pytest.raises(ScoredTwiceError):
            await lenient.run(base_spec(), CANDIDATES, twice)

    def test_claiming_a_window_twice_is_an_error(self) -> None:
        ledger = ScoredWindows()
        window = FeedWindow(START, START + DAY)
        ledger.claim(window)
        with pytest.raises(ScoredTwiceError):
            ledger.claim(window)
        ledger.claim(FeedWindow(START + DAY, START + 2 * DAY))  # a different one is fine


class TestLeakageStopsTheRunBeforeAnythingRuns:
    async def test_overlapping_test_windows_fail_loudly_and_run_nothing(self) -> None:
        backtester = RecordingBacktester()
        first = WalkForwardWindow(
            0, FeedWindow(START, START + 3 * DAY), FeedWindow(START + 3 * DAY, START + 5 * DAY)
        )
        second = WalkForwardWindow(
            1,
            FeedWindow(START + DAY, START + 4 * DAY),
            FeedWindow(START + 4 * DAY, START + 6 * DAY),
        )

        with pytest.raises(LeakageError, match="scored twice"):
            await runner(backtester).run(base_spec(), CANDIDATES, [first, second])

        assert backtester.runs == []

    def test_a_window_cannot_even_be_built_with_test_inside_training(self) -> None:
        with pytest.raises(LeakageError):
            WalkForwardWindow(
                0, FeedWindow(START, START + 3 * DAY), FeedWindow(START + 2 * DAY, START + 4 * DAY)
            )

    async def test_no_windows_and_no_candidates_are_refused(self) -> None:
        with pytest.raises(ValueError):
            await runner(RecordingBacktester()).run(base_spec(), CANDIDATES, [])
        with pytest.raises(ValueError, match="each named once"):
            await runner(RecordingBacktester()).run(base_spec(), [], windows())
        twice = (CANDIDATES[0], CANDIDATES[0])
        with pytest.raises(ValueError, match="each named once"):
            await runner(RecordingBacktester()).run(base_spec(), twice, windows())


class TestAnchoredMode:
    async def test_the_training_window_grows_and_the_tests_still_tile(self) -> None:
        backtester = RecordingBacktester()
        plan = windows(WalkForwardMode.ANCHORED)

        await runner(backtester).run(base_spec(), CANDIDATES, plan)

        assert all(w.train.start == START for w in plan)
        assert [w.train.end - w.train.start for w in plan] == [
            3 * DAY,
            4 * DAY,
            5 * DAY,
            6 * DAY,
            7 * DAY,
        ]


class TestCandidates:
    def test_an_override_the_strategy_does_not_have_is_refused(self) -> None:
        with pytest.raises(ParameterError, match="unknown parameter"):
            ConfigVariants().apply(base_spec().config, ParameterCandidate("bad", {"fast_ema": 3}))

    def test_an_invalid_value_is_refused_by_the_strategys_own_schema(self) -> None:
        with pytest.raises(ParameterError):
            ConfigVariants().apply(
                base_spec().config, ParameterCandidate("bad", {"buy_at": "soon"})
            )

    def test_a_valid_override_changes_only_that_parameter(self) -> None:
        varied = ConfigVariants().apply(base_spec().config, ParameterCandidate("x", {"buy_at": 4}))
        assert (varied.parameters.buy_at, varied.parameters.sell_at) == (4, 5)  # type: ignore[attr-defined]
        assert base_spec().config.parameters.buy_at == 2  # type: ignore[attr-defined]  # untouched

    def test_a_candidate_needs_a_name(self) -> None:
        with pytest.raises(ValueError):
            ParameterCandidate("", {})


class TestSelectorAndObjectives:
    def score(self, name: str, value: str | None) -> TrainingScore:
        return TrainingScore(
            ParameterCandidate(name, {}), None if value is None else Decimal(value)
        )

    def test_the_highest_score_wins_and_ties_go_to_the_earlier_candidate(self) -> None:
        picked = BestScoreSelector().select(
            [self.score("a", "1"), self.score("b", "3"), self.score("c", "3"), self.score("d", "2")]
        )
        assert picked.name == "b"

    def test_unscored_candidates_lose_and_all_unscored_is_an_error(self) -> None:
        assert (
            BestScoreSelector().select([self.score("a", None), self.score("b", "-5")]).name == "b"
        )
        with pytest.raises(NoViableCandidateError):
            BestScoreSelector().select([self.score("a", None)])
        with pytest.raises(NoViableCandidateError):
            BestScoreSelector().select([])

    async def test_a_run_where_nothing_can_be_scored_fails_loudly(self) -> None:
        # one-day training windows have a single daily return: no Sharpe for any candidate
        plan = WalkForwardPlanner().plan(
            WalkForwardMode.ROLLING, START, START + SESSIONS * DAY, DAY, DAY
        )
        with pytest.raises(NoViableCandidateError):
            await runner(RecordingBacktester(), objective=SHARPE).run(base_spec(), CANDIDATES, plan)

    async def test_the_named_objectives_read_the_matching_metric(self) -> None:
        result = await engine(candles()).run(
            BacktestSpec(
                base_spec().config,
                FeedWindow(START, START + 2 * DAY),
                base_spec().starting_cash,
                warmup_bars=0,
            )
        )
        assert NET_PNL.score(result) == result.metrics.trades.net_pnl.amount
        assert TOTAL_RETURN.score(result) == result.metrics.returns.total_return
        assert SHARPE.score(result) == result.metrics.returns.sharpe
        assert MetricObjective("fees", lambda r: r.metrics.trades.fees.amount).name == "fees"


class TestTheAggregate:
    async def test_the_compounded_return_chains_each_windows_out_of_sample_return(self) -> None:
        backtester = RecordingBacktester()
        plan = windows()

        result = await runner(backtester).run(base_spec(), CANDIDATES, plan)

        growth = Decimal(1)
        for outcome in result.outcomes:
            growth *= (
                outcome.test.metrics.ending_equity.amount
                / outcome.test.metrics.starting_cash.amount
            )
        assert abs(result.compounded_test_return - (growth - 1)) < Decimal("1e-25")
        assert result.objective == "net_pnl"
