"""EM-188: feature, cross-sectional and lead-lag evidence becomes the standard experiment report,
under one written-down outcome rule."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from tests.support.experiment_reports import sample_declaration

from emporos.domain.cross_sectional_trials import CrossSectionalTrial
from emporos.domain.experiments import TrialRole
from emporos.domain.feature_trials import FeatureTrial
from emporos.domain.hypotheses import HypothesisDeclaration
from emporos.domain.lead_lag_trials import LeadLagTrial
from emporos.domain.research_experiments import (
    BACKFILLED_NOT_PREDECLARED,
    ExperimentFamily,
    ExperimentOutcomeLabel,
    FindingOutcome,
    ReasonCode,
    VersionStamp,
)
from emporos.research.cross_sectional_ledger import InMemoryCrossSectionalTrialLedger
from emporos.research.experiment_report import (
    BackfilledDeclaration,
    CrossSectionalLedgerEvidence,
    EvidenceThresholds,
    FeatureLedgerEvidence,
    LeadLagLedgerEvidence,
    LedgerExperimentReader,
    LedgerExperimentReportBuilder,
    LedgerOutcomeRule,
)
from emporos.research.hypotheses import InMemoryHypothesisRegistry
from emporos.research.lead_lag_ledger import InMemoryLeadLagTrialLedger
from emporos.research.ledger import InMemoryFeatureTrialLedger

D = Decimal
RECORDED = datetime(2026, 9, 10, tzinfo=UTC)
HYPOTHESIS = HypothesisDeclaration(
    "h-momentum",
    "momentum",
    "v1",
    date(2025, 9, 22),
    date(2026, 9, 18),
    date(2026, 7, 1),
    date(2026, 9, 18),
    datetime(2026, 3, 1, tzinfo=UTC),
)
THRESHOLDS = EvidenceThresholds(min_abs_t=D(2), min_sample=100, min_regimes=2)


def feature_trial(
    trial_id: str,
    *,
    role: TrialRole = TrialRole.TEST,
    axis: str | None = None,
    label: str | None = None,
    sample: int = 500,
    net: str | None = "0.0004",
    t: str | None = "3.1",
    horizon: str = "30m",
    hypothesis: str = "h-momentum",
) -> FeatureTrial:
    return FeatureTrial(
        trial_id, hypothesis, "momentum", "v1", horizon, role, "dataset-a", "costs-a", axis, label,
        sample, D("0.0009"), None if net is None else D(net), D("0.5"), D("0.01"), D("0.001"),
        None if t is None else D(t), RECORDED,
    )  # fmt: skip


def passing_rows() -> list[FeatureTrial]:
    return [
        feature_trial("pooled"),
        feature_trial("vol-high", axis="volatility", label="high"),
        feature_trial("vol-low", axis="volatility", label="low"),
        feature_trial("train", role=TrialRole.TRAIN, net="-0.001", t="-4"),
    ]


def build(trials: list[FeatureTrial], declaration=None):  # type: ignore[no-untyped-def]
    source = FeatureLedgerEvidence.of(HYPOTHESIS, trials)
    rule = LedgerOutcomeRule.standard(THRESHOLDS)
    return LedgerExperimentReportBuilder(rule).build(
        source, declaration or sample_declaration(family=ExperimentFamily.FEATURE), VersionStamp()
    )


def reason(report, code: ReasonCode):  # type: ignore[no-untyped-def]
    return next(r for r in report.reasons if r.code is code)


class TestOutcomeRule:
    def test_a_significant_positive_holdout_in_enough_regimes_is_accepted(self) -> None:
        report = build(passing_rows())

        assert report.outcome is ExperimentOutcomeLabel.ACCEPTED
        assert all(r.outcome is FindingOutcome.PASS for r in report.reasons)

    def test_no_holdout_trial_is_inconclusive_and_says_the_holdout_was_not_evaluated(self) -> None:
        report = build([feature_trial("train", role=TrialRole.TRAIN)])

        assert report.outcome is ExperimentOutcomeLabel.INCONCLUSIVE
        assert reason(report, ReasonCode.HOLDOUT_NOT_EVALUATED).outcome is FindingOutcome.UNKNOWN

    def test_a_thin_holdout_sample_is_inconclusive_however_good_it_looks(self) -> None:
        rows = [feature_trial("pooled", sample=40, net="0.01", t="9")]

        report = build(rows)

        assert report.outcome is ExperimentOutcomeLabel.INCONCLUSIVE
        assert reason(report, ReasonCode.TOO_FEW_TRADES).outcome is FindingOutcome.UNKNOWN

    def test_a_negative_expectancy_on_an_adequate_sample_is_rejected(self) -> None:
        rows = [*passing_rows()[1:], feature_trial("pooled", net="-0.0002", t="-2.5")]

        report = build(rows)

        assert report.outcome is ExperimentOutcomeLabel.REJECTED
        assert reason(report, ReasonCode.NET_PNL_NOT_REAL).outcome is FindingOutcome.FAIL

    def test_a_positive_but_insignificant_edge_on_an_adequate_sample_is_rejected(self) -> None:
        report = build([feature_trial("pooled", net="0.0001", t="0.8")])

        assert report.outcome is ExperimentOutcomeLabel.REJECTED

    def test_one_failing_horizon_rejects_the_hypothesis(self) -> None:
        rows = [*passing_rows(), feature_trial("late", horizon="60m", net="-0.0003", t="-3")]

        assert build(rows).outcome is ExperimentOutcomeLabel.REJECTED

    def test_an_edge_in_one_regime_only_is_rejected_when_enough_were_evaluated(self) -> None:
        rows = [
            feature_trial("pooled"),
            feature_trial("vol-high", axis="volatility", label="high"),
            feature_trial("vol-low", axis="volatility", label="low", net="-0.0005", t="-3"),
        ]

        report = build(rows)

        assert report.outcome is ExperimentOutcomeLabel.REJECTED
        assert reason(report, ReasonCode.TOO_FEW_REGIMES).outcome is FindingOutcome.FAIL

    def test_too_few_regimes_evaluated_is_inconclusive_not_rejected(self) -> None:
        rows = [feature_trial("pooled"), feature_trial("vol-high", axis="volatility", label="high")]

        report = build(rows)

        assert report.outcome is ExperimentOutcomeLabel.INCONCLUSIVE
        assert reason(report, ReasonCode.TOO_FEW_REGIMES).outcome is FindingOutcome.UNKNOWN

    def test_a_missing_statistic_cannot_pass(self) -> None:
        report = build([feature_trial("pooled", t=None)])

        assert report.outcome is ExperimentOutcomeLabel.INCONCLUSIVE
        assert reason(report, ReasonCode.NET_PNL_NOT_REAL).outcome is FindingOutcome.UNKNOWN

    def test_training_trials_never_decide_the_outcome(self) -> None:
        rows = [*passing_rows(), feature_trial("t2", role=TrialRole.TRAIN, net="-5", t="-9")]

        assert build(rows).outcome is ExperimentOutcomeLabel.ACCEPTED

    def test_a_rule_needs_gates_and_thresholds_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            LedgerOutcomeRule([])
        with pytest.raises(ValueError):
            EvidenceThresholds(min_abs_t=D(0))
        with pytest.raises(ValueError):
            EvidenceThresholds(min_sample=0)


class TestReport:
    def test_the_holdout_and_train_periods_come_from_the_hypothesis(self) -> None:
        periods = build(passing_rows()).periods

        assert periods.holdout is not None and periods.train is not None
        assert (periods.holdout.first, periods.holdout.last) == (
            date(2026, 7, 1),
            date(2026, 9, 18),
        )
        assert (periods.train.first, periods.train.last) == (date(2025, 9, 22), date(2026, 6, 30))
        assert periods.validation is None

    def test_pnl_fields_are_not_applicable(self) -> None:
        m = build(passing_rows()).metrics

        assert m.gross_pnl is None and m.net_pnl is None and m.sharpe is None and m.pbo is None
        assert m.max_drawdown is None and m.costs is None

    def test_a_single_pooled_holdout_row_gives_the_headline_expectancy(self) -> None:
        rows = [feature_trial("pooled", net="0.0007")]

        assert build(rows).metrics.expectancy == D("0.0007")

    def test_several_pooled_rows_state_no_headline_and_say_why(self) -> None:
        report = build([*passing_rows(), feature_trial("late", horizon="60m")])

        assert report.metrics.expectancy is None
        assert any("several pooled holdout trials" in n for n in report.notes)

    def test_every_ledger_row_is_carried_as_supporting_evidence(self) -> None:
        report = build(passing_rows())

        evidence = report.supporting["evidence"]
        assert isinstance(evidence, list) and len(evidence) == 4

    def test_the_cost_model_and_dataset_are_recorded(self) -> None:
        report = build(passing_rows())

        assert report.versions.cost_model is not None
        assert report.versions.cost_model.fee_schedule_id == "costs-a"
        assert any("dataset-a" in n for n in report.notes)

    def test_trials_of_other_hypotheses_are_ignored(self) -> None:
        rows = [*passing_rows(), feature_trial("other", hypothesis="h-other", net="-9", t="-9")]

        assert build(rows).outcome is ExperimentOutcomeLabel.ACCEPTED

    def test_a_declaration_of_another_family_is_refused(self) -> None:
        source = FeatureLedgerEvidence.of(HYPOTHESIS, passing_rows())

        with pytest.raises(ValueError, match="cannot be reported under"):
            LedgerExperimentReportBuilder().build(source, sample_declaration(), VersionStamp())


class TestBackfilled:
    def test_a_backfilled_declaration_never_yields_accepted(self) -> None:
        backfilled = BackfilledDeclaration.of(ExperimentFamily.FEATURE, HYPOTHESIS)

        report = build(passing_rows(), backfilled)

        assert report.outcome is ExperimentOutcomeLabel.INCONCLUSIVE
        assert BACKFILLED_NOT_PREDECLARED in report.notes
        assert reason(report, ReasonCode.NOT_PREDECLARED).outcome is FindingOutcome.UNKNOWN

    def test_a_backfilled_rejection_stays_rejected_and_is_marked(self) -> None:
        backfilled = BackfilledDeclaration.of(ExperimentFamily.FEATURE, HYPOTHESIS)

        report = build([feature_trial("pooled", net="-1", t="-9")], backfilled)

        assert report.outcome is ExperimentOutcomeLabel.REJECTED
        assert BACKFILLED_NOT_PREDECLARED in report.notes

    def test_the_backfilled_declaration_invents_nothing(self) -> None:
        d = BackfilledDeclaration.of(ExperimentFamily.FEATURE, HYPOTHESIS)

        assert d.economic_rationale == "backfilled: not pre-declared"
        assert d.falsification == "backfilled: not recorded"
        assert d.declared_at == HYPOTHESIS.declared_at
        assert dict(d.feature_versions) == {"momentum": "v1"}
        assert d.slug == "h-momentum"

    def test_a_hypothesis_id_becomes_a_valid_slug(self) -> None:
        odd = replace(HYPOTHESIS, hypothesis_id="EM-179 / Residual Momentum!")

        assert BackfilledDeclaration.of(ExperimentFamily.FEATURE, odd).slug == (
            "em-179-residual-momentum"
        )


def cross_trial(
    trial_id: str, *, axis: str | None = None, label: str | None = None
) -> CrossSectionalTrial:
    return CrossSectionalTrial(
        trial_id, "h-momentum", "20d", "5d", "top", TrialRole.TEST, "dataset-b", "costs-b", axis,
        label, 300, D("0.001"), D("0.0005"), D("0.52"), D("2.8"), RECORDED,
    )  # fmt: skip


def lead_lag_trial(trial_id: str) -> LeadLagTrial:
    return LeadLagTrial(
        trial_id, "h-momentum", "market", "stock", "NSE:INFY-EQ", "30m", "30m", "up",
        TrialRole.TEST, "dataset-c", "costs-c", None, None, 250, D("0.001"), D("-0.0002"),
        D("0.48"), D("0.02"), D("0.004"), D("-2.2"), RECORDED,
    )  # fmt: skip


class TestOtherFamilies:
    def test_cross_sectional_rows_use_the_net_expectancy(self) -> None:
        source = CrossSectionalLedgerEvidence.of(
            HYPOTHESIS,
            [cross_trial("a"), cross_trial("b", axis="volatility", label="high"),
             cross_trial("c", axis="volatility", label="low")],
        )  # fmt: skip

        report = LedgerExperimentReportBuilder(LedgerOutcomeRule.standard(THRESHOLDS)).build(
            source, sample_declaration(family=ExperimentFamily.CROSS_SECTIONAL), VersionStamp()
        )

        assert report.outcome is ExperimentOutcomeLabel.ACCEPTED
        assert report.declaration.family is ExperimentFamily.CROSS_SECTIONAL

    def test_lead_lag_rows_use_the_cost_adjusted_expectancy(self) -> None:
        source = LeadLagLedgerEvidence.of(HYPOTHESIS, [lead_lag_trial("a")])

        report = LedgerExperimentReportBuilder(LedgerOutcomeRule.standard(THRESHOLDS)).build(
            source, sample_declaration(family=ExperimentFamily.LEAD_LAG), VersionStamp()
        )

        assert report.outcome is ExperimentOutcomeLabel.REJECTED


class TestReader:
    async def reader(self) -> LedgerExperimentReader:
        hypotheses = InMemoryHypothesisRegistry()
        await hypotheses.declare(HYPOTHESIS)
        features = InMemoryFeatureTrialLedger()
        for trial in passing_rows():
            await features.append(trial)
        cross = InMemoryCrossSectionalTrialLedger()
        await cross.append(cross_trial("x"))
        lead_lag = InMemoryLeadLagTrialLedger()
        await lead_lag.append(lead_lag_trial("y"))
        return LedgerExperimentReader(hypotheses, features, cross, lead_lag)

    async def test_it_reads_each_family_from_its_own_ledger(self) -> None:
        reader = await self.reader()

        feature = await reader.read(ExperimentFamily.FEATURE, "h-momentum")
        cross = await reader.read(ExperimentFamily.CROSS_SECTIONAL, "h-momentum")
        lead_lag = await reader.read(ExperimentFamily.LEAD_LAG, "h-momentum")

        assert (len(feature.rows), len(cross.rows), len(lead_lag.rows)) == (4, 1, 1)

    async def test_an_undeclared_hypothesis_is_refused(self) -> None:
        with pytest.raises(LookupError, match="no hypothesis"):
            await (await self.reader()).read(ExperimentFamily.FEATURE, "nope")

    async def test_a_family_without_a_ledger_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not backed by a trial ledger"):
            await (await self.reader()).read(ExperimentFamily.STRATEGY, "h-momentum")


class TestReadAll:
    async def test_it_reads_each_ledger_once_and_makes_one_experiment_per_family_and_hypothesis(
        self,
    ) -> None:
        reader = await TestReader().reader()

        found = await reader.read_all()

        assert sorted((e.family.value, e.hypothesis.hypothesis_id, len(e.rows)) for e in found) == [
            ("cross_sectional", "h-momentum", 1),
            ("feature", "h-momentum", 4),
            ("lead_lag", "h-momentum", 1),
        ]

    async def test_a_hypothesis_no_registry_knows_is_skipped_not_guessed(self) -> None:
        hypotheses = InMemoryHypothesisRegistry()  # declares nothing
        features = InMemoryFeatureTrialLedger()
        await features.append(feature_trial("orphan", hypothesis="h-unknown"))
        reader = LedgerExperimentReader(
            hypotheses, features, InMemoryCrossSectionalTrialLedger(), InMemoryLeadLagTrialLedger()
        )

        assert await reader.read_all() == []
