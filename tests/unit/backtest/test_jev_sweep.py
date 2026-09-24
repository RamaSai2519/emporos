from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.fingerprint import FingerprintContext
from emporos.backtest.jev_incremental import JevIncrementalAnalysis
from emporos.backtest.jev_pnl import BacktestRunner
from emporos.backtest.jev_sweep import (
    JevIncompleteRun,
    JevSweep,
    JevSweepRequest,
    JevVariant,
)
from emporos.backtest.robustness.jev_gate import JevIncrementalPolicy
from emporos.backtest.robustness.paired_bootstrap import PairedDayBootstrap
from emporos.backtest.robustness.trials import InMemoryTrialLedger
from emporos.backtest.robustness.verdict import VerdictReport
from emporos.domain.experiments import Verdict
from emporos.jev.config import JevConfig
from emporos.jev.leakage import JevLeakageError, KnowledgeCutoffGuard
from emporos.jev.models import (
    CONFIRM,
    CONFIRMATION,
    RANKING,
    STRATEGY_SELECTION,
    JevDecision,
    JevRequest,
    failed_decision,
)
from emporos.jev.prompts import DEFAULT_PROMPT
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from tests.support.backtest_engine import BuyThenSell
from tests.support.jev import JEV_T0, RecordingProvider, make_jev_decision
from tests.support.jev_backtest import scenario_engine, scenario_spec

D = Decimal
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
CONFIG = JevConfig(
    model_knowledge_cutoff=date(2025, 1, 31), inr_per_1k_tokens=D("2"), max_retries=0
)
MARGIN = timedelta(days=30)


class _Factory:
    def __init__(self) -> None:
        self.built_with: list[JevMetaDecisionFilter | None] = []

    def build(self, jev_filter: JevMetaDecisionFilter | None) -> BacktestRunner:
        self.built_with.append(jev_filter)
        return scenario_engine(jev_filter)


class _Provider:
    """Answers every question with a confirmation that costs 100 tokens."""

    async def decide(self, request: JevRequest) -> JevDecision:
        return make_jev_decision(request, tokens_used=100)


class _Failing:
    async def decide(self, request: JevRequest) -> JevDecision:
        return failed_decision("test", error="not recorded", requested_at=JEV_T0)


def _request(*variants: JevVariant, config: JevConfig = CONFIG) -> JevSweepRequest:
    return JevSweepRequest(
        spec=scenario_spec(),
        context=FingerprintContext(fee_schedule_id="test-fees"),
        variants=variants or (JevVariant(CONFIRMATION, D("0.6")),),
        config=config,
        prompt=DEFAULT_PROMPT,
        experiment="jev-test",
        strategy_label="buy_then_sell",
        baseline_verdict=VerdictReport(Verdict.REJECTED, ()),
        dataset_version="test bars",
        cost_model="test costs",
    )


def _sweep(
    factory: _Factory, ledger: InMemoryTrialLedger, provider: object | None = None
) -> JevSweep:
    return JevSweep(
        factory=factory,
        provider=provider or _Provider(),  # type: ignore[arg-type]
        ledger=ledger,
        analysis=JevIncrementalAnalysis(PairedDayBootstrap(seed=1, resamples=50)),
        policy=JevIncrementalPolicy.standard(),
        guard=KnowledgeCutoffGuard(MARGIN),
        now=lambda: NOW,
    )


async def test_the_baseline_runs_once_and_every_variant_is_judged() -> None:
    BuyThenSell.reset()
    factory, ledger = _Factory(), InMemoryTrialLedger()

    outcome = await _sweep(factory, ledger).run(
        _request(JevVariant(CONFIRMATION, D("0.6")), JevVariant(RANKING, D("0.5")))
    )

    assert factory.built_with[0] is None
    assert sum(f is None for f in factory.built_with) == 1
    assert len(outcome.variants) == 2
    assert all(v.comparison.fingerprint == outcome.fingerprint for v in outcome.variants)
    assert all(v.tally.requests >= 1 and v.tally.complete for v in outcome.variants)


async def test_a_weak_baseline_caps_every_variant_at_inconclusive() -> None:
    BuyThenSell.reset()

    outcome = await _sweep(_Factory(), InMemoryTrialLedger()).run(_request())

    assert outcome.headline.verdict.verdict is Verdict.INCONCLUSIVE


async def test_every_variant_is_recorded_as_a_trial_before_the_arms_are_priced() -> None:
    BuyThenSell.reset()
    ledger = InMemoryTrialLedger()

    outcome = await _sweep(_Factory(), ledger).run(
        _request(JevVariant(CONFIRMATION, D("0.6")), JevVariant(RANKING, D("0.5")))
    )

    trials = await ledger.all()
    assert {t.candidate for t in trials} == {"jev:confirmation:0.6:v1", "jev:ranking:0.5:v1"}
    assert {t.experiment for t in trials} == {"jev-test"}
    assert outcome.trial_count == 2  # what the Deflated Sharpe was priced at


async def test_replaying_the_same_experiment_adds_no_trials() -> None:
    ledger = InMemoryTrialLedger()
    for _ in range(2):
        BuyThenSell.reset()
        outcome = await _sweep(_Factory(), ledger).run(_request())

    assert len(await ledger.all()) == 1
    assert outcome.trial_count == 1


async def test_a_window_before_the_cutoff_is_refused_before_any_run() -> None:
    factory = _Factory()
    early = JevConfig(model_knowledge_cutoff=date(2026, 1, 1), inr_per_1k_tokens=D("2"))

    with pytest.raises(JevLeakageError):
        await _sweep(factory, InMemoryTrialLedger()).run(_request(config=early))

    assert factory.built_with == []


async def test_a_missing_cutoff_is_refused() -> None:
    with pytest.raises(JevLeakageError, match="no model knowledge cutoff"):
        await _sweep(_Factory(), InMemoryTrialLedger()).run(_request(config=JevConfig()))


async def test_a_run_where_jev_failed_to_answer_is_refused_not_reported() -> None:
    BuyThenSell.reset()

    with pytest.raises(JevIncompleteRun, match="not in the journal"):
        await _sweep(_Factory(), InMemoryTrialLedger(), _Failing()).run(_request())


async def test_the_headline_is_the_arm_with_the_largest_gain_net_of_jev() -> None:
    BuyThenSell.reset()

    outcome = await _sweep(_Factory(), InMemoryTrialLedger()).run(
        _request(JevVariant(CONFIRMATION, D("0.6")), JevVariant(RANKING, D("0.5")))
    )

    gains = [v.evidence.net_of_jev_average_trade_delta for v in outcome.variants]
    best = max(g for g in gains if g is not None)
    assert outcome.headline.evidence.net_of_jev_average_trade_delta == best


def test_strategy_selection_is_the_same_arm_as_ranking() -> None:
    with pytest.raises(ValueError, match="distinct arms"):
        _request(JevVariant(RANKING, D("0.5")), JevVariant(STRATEGY_SELECTION, D("0.50")))


def test_a_sweep_needs_a_variant() -> None:
    with pytest.raises(ValueError, match="at least one variant"):
        JevSweepRequest(**{**_request().__dict__, "variants": ()})  # type: ignore[arg-type]


def test_the_candidate_label_names_the_arm_threshold_and_prompt() -> None:
    assert (
        JevVariant(STRATEGY_SELECTION, D("0.60")).candidate(DEFAULT_PROMPT) == "jev:ranking:0.6:v1"
    )


async def test_the_provider_asked_is_recorded_in_the_recording_double() -> None:
    BuyThenSell.reset()
    provider = RecordingProvider(make_jev_decision(None, decision=CONFIRM, tokens_used=7))

    outcome = await _sweep(_Factory(), InMemoryTrialLedger(), provider).run(_request())

    assert len(provider.requests) == outcome.variants[0].tally.requests
    assert all(r.as_of.date() > date(2025, 3, 2) for r in provider.requests)
