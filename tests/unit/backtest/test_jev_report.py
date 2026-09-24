from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.backtest.experiment_document import ExperimentDocument
from emporos.backtest.fingerprint import FingerprintContext
from emporos.backtest.jev_incremental import JevIncrementalAnalysis
from emporos.backtest.jev_pnl import BacktestRunner
from emporos.backtest.jev_report import JevExperimentReportBuilder, JevProvenance, JevReportSource
from emporos.backtest.jev_sweep import JevSweep, JevSweepOutcome, JevSweepRequest, JevVariant
from emporos.backtest.robustness.jev_gate import JevIncrementalPolicy
from emporos.backtest.robustness.paired_bootstrap import PairedDayBootstrap
from emporos.backtest.robustness.trials import InMemoryTrialLedger
from emporos.backtest.robustness.verdict import VerdictReport
from emporos.cli.experiment_registry import FileExperimentRegistry, PublishOutcome
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import (
    BACKFILLED_RATIONALE,
    DatePair,
    ExperimentFamily,
    ExperimentOutcomeLabel,
    FindingOutcome,
    ReasonCode,
    VersionStamp,
)
from emporos.jev.config import JevConfig
from emporos.jev.leakage import KnowledgeCutoffGuard
from emporos.jev.models import CONFIRMATION, RANKING, JevDecision, JevRequest
from emporos.jev.prompts import DEFAULT_PROMPT
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from tests.support.backtest_engine import BuyThenSell
from tests.support.experiment_reports import sample_declaration
from tests.support.jev import make_jev_decision
from tests.support.jev_backtest import scenario_engine, scenario_spec

D = Decimal
WINDOW = DatePair(date(2026, 1, 5), date(2026, 1, 5))
PROVENANCE = JevProvenance(
    model="vendor/model-a",
    knowledge_cutoff="2025-01-31",
    cutoff_source="vendor model card (test)",
    cutoff_margin_days=30,
    prompt_version="v1",
    prompt_hash=DEFAULT_PROMPT.content_hash,
    provider_mode="replay",
    anonymised=True,
)


class _Factory:
    def build(self, jev_filter: JevMetaDecisionFilter | None) -> BacktestRunner:
        return scenario_engine(jev_filter)


class _Provider:
    async def decide(self, request: JevRequest) -> JevDecision:
        return make_jev_decision(request, tokens_used=100)


async def _outcome(baseline: Verdict = Verdict.REJECTED) -> JevSweepOutcome:
    BuyThenSell.reset()
    sweep = JevSweep(
        _Factory(),
        _Provider(),
        InMemoryTrialLedger(),
        JevIncrementalAnalysis(PairedDayBootstrap(seed=1, resamples=50)),
        JevIncrementalPolicy.standard(),
        KnowledgeCutoffGuard(),
        lambda: datetime(2026, 9, 24, tzinfo=UTC),
    )
    request = JevSweepRequest(
        spec=scenario_spec(),
        context=FingerprintContext(),
        variants=(JevVariant(CONFIRMATION, D("0.6")), JevVariant(RANKING, D("0.5"))),
        config=JevConfig(
            model_knowledge_cutoff=date(2025, 1, 31), inr_per_1k_tokens=D("2"), max_retries=0
        ),
        prompt=DEFAULT_PROMPT,
        experiment="jev-test",
        strategy_label="buy_then_sell",
        baseline_verdict=VerdictReport(baseline, ()),
        dataset_version="bars",
        cost_model="costs",
    )
    return await sweep.run(request)


def _source(outcome: JevSweepOutcome, holdout: DatePair | None = None) -> JevReportSource:
    return JevReportSource(outcome, PROVENANCE, WINDOW, holdout, {"buy_then_sell": "rejected"})


async def test_the_report_is_a_jev_incremental_experiment_that_is_not_accepted() -> None:
    declaration = sample_declaration("jev-test", ExperimentFamily.JEV_INCREMENTAL)

    report = JevExperimentReportBuilder().build(
        _source(await _outcome()), declaration, VersionStamp()
    )

    assert report.declaration.family is ExperimentFamily.JEV_INCREMENTAL
    assert report.outcome is ExperimentOutcomeLabel.INCONCLUSIVE
    codes = {r.code for r in report.primary_reasons}
    assert ReasonCode.JEV_BASELINE_NOT_VALIDATED in codes
    assert report.periods.validation == WINDOW


async def test_a_run_with_no_holdout_says_so_as_a_finding() -> None:
    report = JevExperimentReportBuilder().build(
        _source(await _outcome()),
        sample_declaration("jev-test", ExperimentFamily.JEV_INCREMENTAL),
        VersionStamp(),
    )

    assert any(
        r.code is ReasonCode.HOLDOUT_NOT_RESERVED and r.outcome is FindingOutcome.UNKNOWN
        for r in report.reasons
    )


async def test_a_reserved_holdout_is_carried_into_the_periods() -> None:
    holdout = DatePair(date(2026, 2, 1), date(2026, 2, 28))

    report = JevExperimentReportBuilder().build(
        _source(await _outcome(), holdout),
        sample_declaration("jev-test", ExperimentFamily.JEV_INCREMENTAL),
        VersionStamp(),
    )

    assert report.periods.holdout == holdout
    assert not any(r.code is ReasonCode.HOLDOUT_NOT_RESERVED for r in report.reasons)


async def test_provenance_and_every_variant_are_in_the_published_json() -> None:
    declaration = sample_declaration("jev-test", ExperimentFamily.JEV_INCREMENTAL)
    report = JevExperimentReportBuilder().build(
        _source(await _outcome()), declaration, VersionStamp()
    )

    document = ExperimentDocument().to_json(report)
    supporting = document["supporting"]

    assert supporting["provenance"]["model"] == "vendor/model-a"
    assert supporting["provenance"]["knowledge_cutoff"] == "2025-01-31"
    assert supporting["provenance"]["cutoff_source"] == "vendor model card (test)"
    assert supporting["provenance"]["prompt_hash"] == DEFAULT_PROMPT.content_hash
    assert len(supporting["variants"]) == 2
    variant = supporting["variants"][0]
    assert variant["jev_cost"]["inr_per_1k_tokens"] == "2"
    assert "net_of_jev_pnl" in variant["deltas"]
    assert supporting["baseline"]["trade_count"] >= 0
    json.dumps(document)  # everything is JSON-safe


async def test_the_markdown_carries_the_side_by_side_summary_and_provenance() -> None:
    declaration = sample_declaration("jev-test", ExperimentFamily.JEV_INCREMENTAL)
    report = JevExperimentReportBuilder().build(
        _source(await _outcome()), declaration, VersionStamp()
    )

    text = ExperimentDocument().markdown(report)

    assert "knowledge cutoff 2025-01-31" in text
    assert "vendor model card (test)" in text
    assert "Jev cost" in text
    assert "jev:confirmation:0.6:v1" in text


async def test_the_report_publishes_once_and_republishing_is_a_no_op(tmp_path: Path) -> None:
    declaration = sample_declaration("jev-test", ExperimentFamily.JEV_INCREMENTAL)
    outcome = await _outcome()
    report = JevExperimentReportBuilder().build(_source(outcome), declaration, VersionStamp())
    registry = FileExperimentRegistry(tmp_path)

    assert registry.publish(report) is PublishOutcome.WRITTEN
    again = JevExperimentReportBuilder().build(_source(outcome), declaration, VersionStamp())
    assert registry.publish(again) is PublishOutcome.UNCHANGED


async def test_a_backfilled_declaration_can_never_be_accepted() -> None:
    declaration = replace(
        sample_declaration("jev-test", ExperimentFamily.JEV_INCREMENTAL),
        economic_rationale=BACKFILLED_RATIONALE,
    )

    report = JevExperimentReportBuilder().build(
        _source(await _outcome()), declaration, VersionStamp()
    )

    assert report.outcome is not ExperimentOutcomeLabel.ACCEPTED


async def test_another_family_is_refused() -> None:
    with pytest.raises(ValueError, match="jev_incremental"):
        JevExperimentReportBuilder().build(
            _source(await _outcome()), sample_declaration(), VersionStamp()
        )
