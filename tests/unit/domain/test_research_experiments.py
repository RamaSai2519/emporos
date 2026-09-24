"""The experiment report vocabulary: id shape, declaration and period rules, outcome mapping."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import (
    CostBreakdown,
    DatePair,
    ExperimentDeclaration,
    ExperimentFamily,
    ExperimentId,
    ExperimentMetrics,
    ExperimentOutcomeLabel,
    ExperimentPeriods,
    ExperimentReport,
    FindingOutcome,
    ReasonCode,
    ReasonFinding,
    VersionStamp,
    outcome_of,
)

D = Decimal


def declaration(**changes: object) -> ExperimentDeclaration:
    base = ExperimentDeclaration(
        family=ExperimentFamily.STRATEGY,
        slug="orb-v2",
        hypothesis="Opening-range breakouts continue on high-volume days.",
        economic_rationale="Order-flow imbalance at the open persists for the first hour.",
        falsification="Net expectancy after costs is not positive out of sample.",
        parameter_grid={"range_minutes": ("15", "30")},
        feature_versions={"opening_range": "1"},
        declared_at=datetime(2026, 9, 24, 3, 0, tzinfo=UTC),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def pair(a: str, b: str) -> DatePair:
    return DatePair(date.fromisoformat(a), date.fromisoformat(b))


def report(outcome: ExperimentOutcomeLabel, **changes: object) -> ExperimentReport:
    base = ExperimentReport(
        experiment_id=ExperimentId("EXP-20260924-orb-v2-0123abcd"),
        declaration=declaration(),
        versions=VersionStamp(),
        periods=ExperimentPeriods(holdout=pair("2026-09-01", "2026-09-20")),
        metrics=ExperimentMetrics(),
        outcome=outcome,
        reasons=(
            ReasonFinding(ReasonCode.TOO_FEW_TRADES, "enough trades", FindingOutcome.PASS, ""),
        ),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


class TestOutcomeOf:
    @pytest.mark.parametrize(
        ("verdict", "label"),
        [
            (Verdict.VALIDATED, ExperimentOutcomeLabel.ACCEPTED),
            (Verdict.INCONCLUSIVE, ExperimentOutcomeLabel.INCONCLUSIVE),
            (Verdict.REJECTED, ExperimentOutcomeLabel.REJECTED),
        ],
    )
    def test_maps_the_persisted_verdict_to_the_report_wording(
        self, verdict: Verdict, label: ExperimentOutcomeLabel
    ) -> None:
        assert outcome_of(verdict) is label

    def test_every_verdict_is_mapped(self) -> None:
        assert {outcome_of(v) for v in Verdict} == set(ExperimentOutcomeLabel)


class TestExperimentId:
    def test_has_the_declared_date_slug_and_digest(self) -> None:
        assert str(ExperimentId.of(date(2026, 9, 24), "orb-v2", "0123abcd")) == (
            "EXP-20260924-orb-v2-0123abcd"
        )

    @pytest.mark.parametrize(
        "bad", ["", "EXP-2026-orb-0123abcd", "EXP-20260924-Orb-0123abcd", "EXP-20260924-orb-XYZ"]
    )
    def test_refuses_a_malformed_id(self, bad: str) -> None:
        with pytest.raises(ValueError):
            ExperimentId(bad)

    def test_equal_ids_are_interchangeable(self) -> None:
        a, b = ExperimentId("EXP-20260924-orb-0123abcd"), ExperimentId("EXP-20260924-orb-0123abcd")

        assert a == b and hash(a) == hash(b) and {a, b} == {a}

    def test_refuses_a_bad_slug_or_digest(self) -> None:
        with pytest.raises(ValueError):
            ExperimentId.of(date(2026, 9, 24), "Bad Slug", "0123abcd")
        with pytest.raises(ValueError):
            ExperimentId.of(date(2026, 9, 24), "ok", "nothex!!")


class TestDeclaration:
    @pytest.mark.parametrize("field", ["hypothesis", "economic_rationale", "falsification"])
    def test_refuses_an_empty_claim(self, field: str) -> None:
        with pytest.raises(ValueError):
            declaration(**{field: "  "})

    def test_refuses_a_naive_time_or_bad_slug(self) -> None:
        with pytest.raises(ValueError):
            declaration(declared_at=datetime(2026, 9, 24))
        with pytest.raises(ValueError):
            declaration(slug="Not A Slug")

    def test_canonical_form_is_independent_of_key_order(self) -> None:
        a = declaration(parameter_grid={"a": ("1",), "b": ("2",)})
        b = declaration(parameter_grid={"b": ("2",), "a": ("1",)})

        assert list(a.canonical()) == list(b.canonical())
        assert a.canonical() == b.canonical()


class TestPeriods:
    def test_validation_must_end_before_the_holdout_starts(self) -> None:
        with pytest.raises(ValueError):
            ExperimentPeriods(
                validation=pair("2026-08-01", "2026-09-05"),
                holdout=pair("2026-09-01", "2026-09-20"),
            )

    def test_train_must_end_before_the_holdout_starts(self) -> None:
        with pytest.raises(ValueError):
            ExperimentPeriods(
                train=pair("2026-08-01", "2026-09-01"), holdout=pair("2026-09-01", "2026-09-20")
            )

    def test_a_range_cannot_run_backwards(self) -> None:
        with pytest.raises(ValueError):
            pair("2026-09-02", "2026-09-01")

    def test_periods_may_be_absent(self) -> None:
        assert ExperimentPeriods().holdout is None


class TestCostBreakdown:
    def test_parts_must_add_up_to_the_total(self) -> None:
        CostBreakdown(D(1), D(2), D(3), D(4), D(10), None, None)
        with pytest.raises(ValueError):
            CostBreakdown(D(1), D(2), D(3), D(4), D(11), None, None)


class TestReport:
    def test_an_accepted_report_needs_a_reserved_holdout(self) -> None:
        with pytest.raises(ValueError):
            report(ExperimentOutcomeLabel.ACCEPTED, periods=ExperimentPeriods())

    def test_an_accepted_report_cannot_carry_an_unknown_reason(self) -> None:
        unknown = ReasonFinding(
            ReasonCode.HOLDOUT_NOT_EVALUATED, "holdout", FindingOutcome.UNKNOWN, ""
        )

        with pytest.raises(ValueError):
            report(ExperimentOutcomeLabel.ACCEPTED, reasons=(unknown,))

    def test_a_clean_accepted_report_is_valid(self) -> None:
        assert report(ExperimentOutcomeLabel.ACCEPTED).outcome is ExperimentOutcomeLabel.ACCEPTED

    def test_primary_reasons_are_the_failures_else_the_unknowns(self) -> None:
        ok = ReasonFinding(ReasonCode.TOO_FEW_TRADES, "a", FindingOutcome.PASS, "")
        unknown = ReasonFinding(ReasonCode.TOO_LITTLE_HISTORY, "b", FindingOutcome.UNKNOWN, "")
        fail = ReasonFinding(ReasonCode.ADVERSE_COSTS, "c", FindingOutcome.FAIL, "")

        assert report(
            ExperimentOutcomeLabel.REJECTED, reasons=(ok, unknown, fail)
        ).primary_reasons == (fail,)
        assert report(
            ExperimentOutcomeLabel.INCONCLUSIVE, reasons=(ok, unknown)
        ).primary_reasons == (unknown,)
