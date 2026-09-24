"""EM-188: a curation record becomes the standard experiment report, without being re-judged."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.backtest.cost_breakdown import CostBreakdownCalculator
from emporos.backtest.curation import CurationRecord
from emporos.backtest.experiment_report import (
    CalendarDays,
    CurationExperimentReportBuilder,
    ExperimentReportBuilder,
)
from emporos.backtest.feed import FeedWindow
from emporos.backtest.robustness.portfolio_economics import PortfolioCostModel
from emporos.backtest.robustness.verdict import GateOutcome, GateResult, VerdictReport
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import (
    ExperimentFamily,
    ExperimentOutcomeLabel,
    FindingOutcome,
    ReasonCode,
    VersionStamp,
)
from emporos.domain.sizing import DeclaredSize, SizeSource
from tests.support.backtest_engine import FixedSchedule
from tests.support.experiment_reports import sample_declaration
from tests.unit.backtest.test_curation_run import FIRST_DAY, HOLDOUT_DAYS, curate, reservation

DECLARATION = sample_declaration()
RISK_LIMIT = Decimal(25_000)
SIZE = DeclaredSize(RISK_LIMIT, SizeSource.RISK_DEFAULT)


def build(record: CurationRecord, versions: VersionStamp | None = None):  # type: ignore[no-untyped-def]
    return CurationExperimentReportBuilder(SIZE, RISK_LIMIT).build(
        record, DECLARATION, versions or VersionStamp()
    )


def validated(record: CurationRecord) -> CurationRecord:
    """The same record, but with a robustness verdict of VALIDATED (every gate passing)."""
    assert record.robustness is not None
    gates = tuple(
        GateResult(g.name, GateOutcome.PASS, g.detail, g.code)
        for g in record.robustness.verdict.gates
    )
    verdict = VerdictReport(Verdict.VALIDATED, gates)
    return replace(record, robustness=replace(record.robustness, verdict=verdict))


def test_the_builder_is_the_family_neutral_seam() -> None:
    builder: ExperimentReportBuilder[CurationRecord] = CurationExperimentReportBuilder(
        SIZE, RISK_LIMIT
    )

    assert builder is not None


class TestOutcomeAndReasons:
    async def test_the_outcome_is_the_robustness_verdict_reworded(self) -> None:
        record, _ = await curate(reservation())
        assert record.robustness is not None

        report = build(record)

        expected = {
            Verdict.VALIDATED: ExperimentOutcomeLabel.ACCEPTED,
            Verdict.INCONCLUSIVE: ExperimentOutcomeLabel.INCONCLUSIVE,
            Verdict.REJECTED: ExperimentOutcomeLabel.REJECTED,
        }[record.robustness.verdict.verdict]
        assert report.outcome is expected

    async def test_every_gate_becomes_a_reason_with_its_code(self) -> None:
        record, _ = await curate(reservation())
        assert record.robustness is not None

        report = build(record)

        gates = record.robustness.verdict.gates
        assert [(r.code, r.name, r.outcome.value) for r in report.reasons] == [
            (g.code, g.name, g.outcome.value) for g in gates
        ]
        assert all(r.code is not None for r in report.reasons)

    async def test_a_validated_run_with_a_reserved_holdout_is_accepted(self) -> None:
        record, _ = await curate(reservation())

        report = build(validated(record))

        assert report.outcome is ExperimentOutcomeLabel.ACCEPTED
        assert all(r.outcome is FindingOutcome.PASS for r in report.reasons)

    async def test_a_validated_run_with_no_holdout_is_only_inconclusive(self) -> None:
        record, _ = await curate(None)

        report = build(validated(record))

        assert report.outcome is ExperimentOutcomeLabel.INCONCLUSIVE
        assert report.periods.holdout is None
        last = report.reasons[-1]
        assert (last.code, last.outcome) == (
            ReasonCode.HOLDOUT_NOT_RESERVED,
            FindingOutcome.UNKNOWN,
        )

    async def test_a_rejected_run_stays_rejected_without_a_holdout(self) -> None:
        record, _ = await curate(None)
        assert record.robustness is not None
        failing = GateResult("g", GateOutcome.FAIL, "x", ReasonCode.ADVERSE_COSTS)
        rejected = replace(
            record,
            robustness=replace(
                record.robustness, verdict=VerdictReport(Verdict.REJECTED, (failing,))
            ),
        )

        report = build(rejected)

        assert report.outcome is ExperimentOutcomeLabel.REJECTED
        assert report.reasons[-1].code is ReasonCode.HOLDOUT_NOT_RESERVED

    async def test_without_a_robustness_assessment_the_criteria_stand_in_and_never_accept(
        self,
    ) -> None:
        record, _ = await curate(reservation())

        report = build(replace(record, robustness=None))

        assert report.outcome in {
            ExperimentOutcomeLabel.INCONCLUSIVE,
            ExperimentOutcomeLabel.REJECTED,
        }
        assert all(
            r.code is None for r in report.reasons if r.code is not ReasonCode.HOLDOUT_NOT_RESERVED
        )
        assert report.periods.holdout is None

    async def test_only_a_strategy_declaration_may_describe_a_curation(self) -> None:
        record, _ = await curate(reservation())

        with pytest.raises(ValueError):
            CurationExperimentReportBuilder(SIZE, RISK_LIMIT).build(
                record, sample_declaration(family=ExperimentFamily.FEATURE), VersionStamp()
            )


class TestPeriods:
    async def test_train_validation_and_holdout_are_recorded_in_order(self) -> None:
        record, _ = await curate(reservation())

        periods = build(record).periods

        assert periods.train is not None and periods.validation is not None
        assert periods.holdout is not None
        assert periods.train.first == FIRST_DAY
        assert periods.validation.last < periods.holdout.first
        assert (periods.holdout.last - periods.holdout.first).days + 1 == HOLDOUT_DAYS

    async def test_without_provenance_only_validation_is_known(self) -> None:
        record, _ = await curate(None)

        periods = build(record).periods

        assert periods.train is None and periods.holdout is None
        assert periods.validation is not None

    def test_a_window_is_the_inclusive_ist_days_it_covers(self) -> None:
        from tests.unit.backtest.test_curation_run import PLAN

        start, end = PLAN.bounds()

        days = CalendarDays.of(FeedWindow(start, end))

        assert days.first == PLAN.first_day and days.last == PLAN.last_day
        assert CalendarDays.of(FeedWindow(start, start + timedelta(hours=1))).first == days.first


class TestMetrics:
    async def test_headline_numbers_are_the_pooled_out_of_sample_ones(self) -> None:
        record, _ = await curate(reservation())
        stats = record.pooled.statistics

        m = build(record).metrics

        assert m.net_pnl == stats.net_pnl.amount and m.gross_pnl == stats.gross_pnl.amount
        assert m.trade_count == stats.count
        assert m.expectancy == stats.expectancy and m.profit_factor == stats.profit_factor
        assert m.max_drawdown == max(record.pooled.window_drawdowns)

    async def test_sharpe_dsr_and_pbo_come_from_the_robustness_evidence(self) -> None:
        record, _ = await curate(reservation())
        assert record.robustness is not None

        m = build(record).metrics

        assert m.sharpe == record.robustness.deflated_sharpe.annualised_sharpe
        assert m.deflated_sharpe == record.robustness.deflated_sharpe.deflated_sharpe
        assert m.pbo == record.robustness.pbo.probability_of_overfitting

    async def test_regimes_add_up_to_the_windows_they_came_from(self) -> None:
        record, _ = await curate(reservation())
        assert record.robustness is not None

        m = build(record).metrics

        traded = sum(
            s.count for w in record.robustness.window_performance for s in w.by_regime.values()
        )
        assert sum(r.count for r in m.by_regime.values()) == traded
        assert all(
            r.win_rate is not None and Decimal(0) <= r.win_rate <= 1 for r in m.by_regime.values()
        )
        assert [w.index for w in m.by_window] == [
            w.index for w in record.robustness.window_performance
        ]

    async def test_the_cost_breakdown_sums_to_its_total(self) -> None:
        model = PortfolioCostModel(FixedSchedule().schedule_for(FIRST_DAY), Decimal(2), Decimal(5))
        record, _ = await curate(reservation(), CostBreakdownCalculator(model))

        costs = build(record).metrics.costs

        assert costs is not None
        assert costs.total == costs.brokerage + costs.statutory + costs.spread + costs.slippage

    async def test_costs_are_absent_when_no_cost_model_was_given(self) -> None:
        record, _ = await curate(reservation())

        assert build(record).metrics.costs is None


class TestVersionsAndIdentity:
    async def test_the_dataset_and_candidate_hashes_are_filled_from_the_record(self) -> None:
        record, _ = await curate(reservation())

        versions = build(record, VersionStamp(code_revision="abc1234")).versions

        assert versions.code_revision == "abc1234"
        assert versions.dataset is not None and versions.dataset.first == FIRST_DAY
        assert dict(versions.candidate_behaviour_hashes) == dict(record.behaviour_hashes)

    async def test_the_id_follows_the_declaration_not_the_run(self) -> None:
        record, _ = await curate(reservation())
        other, _ = await curate(None)

        assert build(record).experiment_id == build(other).experiment_id

    async def test_the_robustness_evidence_is_carried_verbatim_as_supporting(self) -> None:
        record, _ = await curate(reservation())

        supporting = build(record).supporting

        assert set(supporting) == {"robustness", "sizing"}
        assert "monte_carlo" in supporting["robustness"]  # type: ignore[operator]

    async def test_the_report_states_the_size_it_was_judged_at(self) -> None:
        record, _ = await curate(reservation())

        report = build(record)

        assert report.supporting["sizing"] == {
            "position_value": "25000",
            "source": "risk_default",
            "risk_limit": "25000",
            "requires_operator_risk_change": False,
        }
        assert any("position value of 25,000.00 rupees (not declared" in n for n in report.notes)

    async def test_a_size_above_the_risk_limit_is_flagged_as_needing_an_operator_decision(
        self,
    ) -> None:
        record, _ = await curate(reservation())
        big = DeclaredSize(Decimal(100_000))

        report = CurationExperimentReportBuilder(big, RISK_LIMIT).build(
            record, DECLARATION, VersionStamp()
        )

        assert report.supporting["sizing"]["requires_operator_risk_change"] is True  # type: ignore[index]
        assert any(
            "declared before the run" in n and "operator risk change" in n for n in report.notes
        )
