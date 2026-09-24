"""EM-187: the whole `jev-compare` flow against fakes for Mongo and the model: record once, then
replay the same experiment and get the same answer with no live call."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from emporos.backtest.robustness.trials import InMemoryTrialLedger
from emporos.backtest.universe import AsOfInstruments, InstrumentEra
from emporos.cli.backtest_runtime import BacktestRuntime
from emporos.cli.experiment_declarations import ExperimentDeclarationLoader
from emporos.cli.jev_commands import (
    JevCompareRequest,
    JevCompareRun,
    JevStores,
    ProviderMode,
)
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.experiments import Verdict
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.domain.research_experiments import ExperimentFamily, ExperimentOutcomeLabel
from emporos.domain.verdicts import RecordedVerdict
from emporos.jev.budget import JevBudgetExceeded
from emporos.jev.config import JevConfig
from emporos.jev.models import JevDecision, JevRequest
from emporos.jev.prompts import DEFAULT_PROMPT
from emporos.jev.replay import InMemoryJevDecisionJournal
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_momentum import DAYS, momentum_candles
from tests.support.jev import make_jev_decision
from tests.support.jev_backtest import trend_daily_bars
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, momentum_raw

FIRST = date(2026, 1, 5)
LAST = date(2026, 1, 5 + DAYS - 1)
LONG_AGO = datetime(2025, 1, 1, tzinfo=UTC)
PLAN = """\
strategies:
  - {strategy}
constraints:
  max_simultaneous_positions: 3
  max_risk_per_trade: "500"
  max_portfolio_risk: "1500"
"""
DECLARATION = f"""\
family: jev_incremental
slug: jev-run-test
hypothesis: h
economic_rationale: r
falsification: f
parameter_grid:
  model: ["vendor/model-a"]
  model_knowledge_cutoff: ["2025-01-01"]
  cutoff_source: ["test fixture"]
  inr_per_1k_tokens: ["0.05"]
  prompt_version: ["v1"]
  prompt_hash: ["{DEFAULT_PROMPT.content_hash}"]
  mode: ["confirmation", "ranking"]
  threshold: ["0.5", "0.7"]
declared_at: 2026-09-24T09:00:00+05:30
"""


class _Live:
    """A model that confirms everything, and counts how often it was asked."""

    def __init__(self, config: JevConfig) -> None:
        self.calls = 0
        self._model = config.model

    async def decide(self, request: JevRequest) -> JevDecision:
        self.calls += 1
        return make_jev_decision(
            request,
            tokens_used=100,
            prompt_hash=DEFAULT_PROMPT.content_hash,
            model=self._model,
            latency_ms=7,
        )


class _NoVerdicts:
    async def latest(self, strategy: str) -> RecordedVerdict | None:
        return None


class _Stores:
    def __init__(self) -> None:
        self.stores = JevStores(_NoVerdicts(), InMemoryJevDecisionJournal(), InMemoryTrialLedger())

    def open(self, database: object, record_trials: bool) -> JevStores:
        return self.stores


@asynccontextmanager
async def _runtime(settings: Settings) -> AsyncIterator[BacktestRuntime]:
    def instrument(token: str, symbol: str) -> Instrument:
        return Instrument(Exchange.NSE, token, symbol, symbol, 1, Money.of("0.05"))

    eras = AsOfInstruments(
        [
            InstrumentEra(instrument("1001", "ALPHA-EQ"), LONG_AGO, None),
            InstrumentEra(instrument("1002", "BETA-EQ"), LONG_AGO, None),
        ]
    )
    # Daily bars for the regime warm-up: without a regime the scanner produces no candidates.
    daily = [*trend_daily_bars(INSTRUMENT), *trend_daily_bars(OTHER_INSTRUMENT)]
    reader = InMemoryCandles([*momentum_candles(), *daily])
    yield BacktestRuntime(reader, eras, object())  # type: ignore[arg-type]


def _request(tmp_path: Path, provider: ProviderMode) -> JevCompareRequest:
    strategy = tmp_path / "momentum_v1.yaml"
    strategy.write_text(yaml.safe_dump(momentum_raw()), encoding="utf-8")
    plan = tmp_path / "plan.yaml"
    plan.write_text(PLAN.format(strategy=strategy), encoding="utf-8")
    declaration = tmp_path / "jev-run-test.yaml"
    declaration.write_text(DECLARATION, encoding="utf-8")
    return JevCompareRequest(
        declaration=ExperimentDeclarationLoader().load(declaration),
        plan=plan,
        first=FIRST,
        last=LAST,
        modes=None,
        thresholds=None,
        provider=provider,
        anonymise=True,
        max_requests=500,
        assume_yes=True,
        benchmark=Path("config/robustness/benchmark.yaml"),
        experiments_dir=tmp_path / "experiments",
        assume_earliest_fees=True,
        assume_current_universe=False,
        record_trials=False,
    )


def _run(stores: _Stores, lives: list[_Live], key: str | None = "vck_test") -> JevCompareRun:
    def live(config: JevConfig, _key: str) -> _Live:
        lives.append(_Live(config))
        return lives[-1]

    settings = Settings.default().model_copy(update={"vercel_gateway_key": key})
    return JevCompareRun(settings, _runtime, stores, live)


async def test_record_then_replay_reproduces_the_experiment_without_asking_the_model(
    tmp_path: Path,
) -> None:
    stores, lives = _Stores(), []
    recorded, source, _ = await _run(stores, lives).run(_request(tmp_path, ProviderMode.RECORD))

    replayed, replay_source, versions = await _run(stores, lives).run(
        _request(tmp_path, ProviderMode.REPLAY)
    )

    assert len(recorded.variants) == 4  # two modes x two thresholds, the declared grid
    assert lives[0].calls > 0  # the model was asked while recording
    assert len(lives) == 1  # replay built no live provider at all
    for before, after in zip(recorded.variants, replayed.variants, strict=True):
        assert before.evidence == after.evidence
        assert after.tally.requests == before.tally.requests
    assert replayed.fingerprint == recorded.fingerprint
    assert replayed.trial_count == recorded.trial_count == 4
    assert replay_source.baseline_standings == {"momentum_v1": "none"}
    assert versions.candidate_behaviour_hashes["momentum_v1"].startswith("sha256:")


async def test_a_baseline_that_was_never_validated_caps_the_result(tmp_path: Path) -> None:
    outcome, _, _ = await _run(_Stores(), []).run(_request(tmp_path, ProviderMode.RECORD))

    assert all(v.verdict.verdict is Verdict.INCONCLUSIVE for v in outcome.variants)


async def test_the_recording_is_the_anonymised_question(tmp_path: Path) -> None:
    stores = _Stores()
    await _run(stores, []).run(_request(tmp_path, ProviderMode.RECORD))

    journal = stores.stores.journal
    assert isinstance(journal, InMemoryJevDecisionJournal)
    assert len(journal) > 0
    records = list(journal._records.values())
    assert all(r.symbol.startswith("INSTR_") for r in records)


async def test_replay_with_an_empty_journal_is_refused_not_reported(tmp_path: Path) -> None:
    with pytest.raises(EmporosError, match="not in the journal"):
        await _run(_Stores(), []).run(_request(tmp_path, ProviderMode.REPLAY))


async def test_the_call_cap_stops_a_recording_run(tmp_path: Path) -> None:
    request = _request(tmp_path, ProviderMode.RECORD)
    capped = JevCompareRequest(**{**request.__dict__, "max_requests": 1})

    with pytest.raises(JevBudgetExceeded):
        await _run(_Stores(), []).run(capped)


async def test_the_declared_family_is_a_jev_experiment(tmp_path: Path) -> None:
    request = _request(tmp_path, ProviderMode.REPLAY)

    assert request.declaration.family is ExperimentFamily.JEV_INCREMENTAL
    assert ExperimentOutcomeLabel.ACCEPTED.value == "accepted"
