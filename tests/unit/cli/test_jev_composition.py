"""EM-187: the plan is read strictly, the holdout is carved off before anything runs, and the
provider stack is layered so the journal sees exactly what the model would."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from emporos.backtest.jev_pnl import BacktestRunner
from emporos.backtest.multi_engine import MultiStrategyBacktestEngine
from emporos.backtest.pricing import PassThroughGate
from emporos.cli.jev_composition import (
    DEFAULT_PLAN,
    BaselineStandings,
    HoldoutSplit,
    JevPlanLoader,
    JevProviderStack,
    MultiStrategyEngineFactory,
    TradingDayWindow,
)
from emporos.core.errors import ConfigurationError
from emporos.domain.experiments import Verdict
from emporos.domain.money import Money
from emporos.domain.research_experiments import DatePair
from emporos.domain.verdicts import RecordedVerdict, Standing
from emporos.jev.prompts import DEFAULT_PROMPT
from emporos.jev.replay import NOT_RECORDED, InMemoryJevDecisionJournal
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import FixedSchedule, FixedTicks, registry
from tests.support.jev import RecordingProvider, make_jev_decision, make_jev_request
from tests.support.strategies import make_config

VALID = """\
strategies:
  - config/strategies/orb_v1.yaml
constraints:
  max_simultaneous_positions: 3
  max_risk_per_trade: "500"
  max_portfolio_risk: 1500
holdout_days: 30
"""


def _plan(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestPlan:
    def test_the_shipped_plan_is_valid(self) -> None:
        plan = JevPlanLoader().load(DEFAULT_PLAN)

        assert plan.strategy_files
        assert plan.holdout_days is not None

    def test_a_valid_plan_is_read_field_for_field(self, tmp_path: Path) -> None:
        plan = JevPlanLoader().load(_plan(tmp_path, VALID))

        assert plan.strategy_files == (Path("config/strategies/orb_v1.yaml"),)
        assert plan.max_risk_per_trade == Money.of("500")
        assert plan.max_portfolio_risk == Money.of("1500")
        assert plan.holdout_days == 30
        constraints = plan.constraints(Money.of("50000"))
        assert constraints.production_capital == Money.of("50000")
        assert constraints.max_simultaneous_positions == 3

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            (VALID + "surprise: 1\n", "unknown plan key"),
            (VALID.replace("constraints:", "constraints:\n  extra: 1"), "unknown constraints key"),
            (VALID.replace('"500"', "500.5"), "quoted string"),
            (VALID.replace("holdout_days: 30", "holdout_days: 0"), "holdout_days"),
            (VALID.replace("  - config/strategies/orb_v1.yaml\n", ""), "non-empty list"),
            ("constraints: 3\nstrategies: [a]\n", "constraints must be a mapping"),
            ("- a\n- b\n", "must be a mapping"),
            (VALID.replace("  max_portfolio_risk: 1500\n", ""), "missing"),
        ],
    )
    def test_a_bad_plan_is_refused(self, tmp_path: Path, text: str, message: str) -> None:
        with pytest.raises(ConfigurationError, match=message):
            JevPlanLoader().load(_plan(tmp_path, text))

    def test_an_unreadable_plan_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="cannot read"):
            JevPlanLoader().load(tmp_path / "missing.yaml")


class TestHoldoutSplit:
    def test_the_last_days_are_reserved_and_the_run_ends_before_them(self) -> None:
        split = HoldoutSplit().split(date(2026, 1, 1), date(2026, 3, 31), 30)

        assert split.holdout == DatePair(date(2026, 3, 2), date(2026, 3, 31))
        assert split.last == date(2026, 3, 1)
        assert split.first == date(2026, 1, 1)

    def test_no_holdout_reads_the_whole_range(self) -> None:
        split = HoldoutSplit().split(date(2026, 1, 1), date(2026, 3, 31), None)

        assert (split.last, split.holdout) == (date(2026, 3, 31), None)

    def test_a_holdout_that_leaves_nothing_to_run_is_refused(self) -> None:
        with pytest.raises(ValueError, match="leaves no days"):
            HoldoutSplit().split(date(2026, 1, 1), date(2026, 1, 20), 30)

    def test_a_backwards_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="first day"):
            HoldoutSplit().split(date(2026, 2, 1), date(2026, 1, 1), None)


def test_the_window_runs_from_midnight_ist_to_midnight_ist_after_the_last_day() -> None:
    window = TradingDayWindow.of(date(2026, 1, 5), date(2026, 1, 6))

    assert window.start == datetime(2026, 1, 4, 18, 30, tzinfo=UTC)
    assert window.end == datetime(2026, 1, 6, 18, 30, tzinfo=UTC)


def test_the_factory_builds_a_real_engine_for_each_arm() -> None:
    factory = MultiStrategyEngineFactory(
        InMemoryCandles([]), registry(), FixedTicks(), lambda: FixedSchedule(), PassThroughGate
    )

    baseline: BacktestRunner = factory.build(None)

    assert isinstance(baseline, MultiStrategyBacktestEngine)
    assert factory.build(None) is not baseline  # arms never share an engine


class TestProviderStack:
    def _stack(self, anonymise: bool) -> tuple[JevProviderStack, InMemoryJevDecisionJournal]:
        journal = InMemoryJevDecisionJournal()
        return JevProviderStack(journal, DEFAULT_PROMPT, "test-model", "salt", anonymise), journal

    async def test_replay_answers_only_from_the_journal(self) -> None:
        stack, _ = self._stack(anonymise=True)

        decision = await stack.replay().decide(make_jev_request())

        assert decision.error == NOT_RECORDED

    async def test_record_journals_the_anonymised_question_the_model_saw(self) -> None:
        stack, journal = self._stack(anonymise=True)
        live = RecordingProvider(
            make_jev_decision(None, prompt_hash=DEFAULT_PROMPT.content_hash, model="test-model")
        )

        await stack.record(live, "confirmation").decide(make_jev_request())

        assert live.requests[0].symbol.startswith("INSTR_")
        recorded = await journal.get(
            live.requests[0].request_hash(), DEFAULT_PROMPT.content_hash, "test-model"
        )
        assert recorded is not None and recorded.symbol.startswith("INSTR_")

    async def test_without_anonymisation_the_real_symbol_is_asked(self) -> None:
        stack, _ = self._stack(anonymise=False)
        live = RecordingProvider(
            make_jev_decision(None, prompt_hash=DEFAULT_PROMPT.content_hash, model="test-model")
        )

        await stack.record(live, "ranking").decide(make_jev_request())

        assert live.requests[0].symbol == "NSE:RELIANCE-EQ"


class TestBaselineStandings:
    def _verdict(self, behaviour_hash: str, verdict: Verdict) -> RecordedVerdict:
        return RecordedVerdict(
            "momentum_v1", behaviour_hash, verdict, (), "50000", "2026-01-01", "2026-06-30",
            "curation", "curation", datetime(2026, 7, 1, tzinfo=UTC),
        )  # fmt: skip

    def test_each_strategy_stands_against_its_current_config(self) -> None:
        from emporos.strategies.snapshot import ConfigSnapshotter

        config = make_config(name="momentum_v1")
        current = ConfigSnapshotter().take(config).behaviour_hash
        book = {"momentum_v1": self._verdict(current, Verdict.REJECTED)}

        standings = BaselineStandings(book.get).of({"momentum_v1": config})

        assert standings == {"momentum_v1": Standing.REJECTED}

    def test_an_edited_config_is_stale_and_an_uncurated_one_is_none(self) -> None:
        config = make_config(name="momentum_v1")
        other = make_config(name="other_v1")
        book = {"momentum_v1": self._verdict("sha256:old", Verdict.VALIDATED)}

        standings = BaselineStandings(book.get).of({"momentum_v1": config, "other_v1": other})

        assert standings == {"momentum_v1": Standing.STALE, "other_v1": Standing.NONE}
