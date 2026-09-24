from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from emporos.backtest.jev_incremental import JevIncrementalAnalysis, JevIncrementalEvidence
from emporos.backtest.robustness.jev_gate import (
    DrawdownNotWorse,
    JevGateContext,
    JevIncrementalPolicy,
    NetExpectancyLift,
    SignificantLift,
)
from emporos.backtest.robustness.paired_bootstrap import PairedDayBootstrap
from emporos.backtest.robustness.trials import TrialStatistics
from emporos.backtest.robustness.verdict import GateOutcome, VerdictReport
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import ReasonCode
from emporos.opportunity.jev_experiment import JevRunSummary
from tests.support.jev_comparison import comparison, metrics, winning_trades

D = Decimal
_TRIALS = TrialStatistics(count=1, scored=1, sharpe_variance=None)


def _verdict(verdict: Verdict) -> VerdictReport:
    return VerdictReport(verdict, ())


def _evidence(
    *,
    baseline_step: int = 100,
    treatment_step: int = 200,
    tokens: int = 1000,
    baseline_trades: int = 10,
    treatment_trades: int = 10,
    days: int = 40,
) -> JevIncrementalEvidence:
    """Equity that rises `step` a day: a bigger step is a better arm, with a constant daily gain
    so the paired interval is entirely on one side of zero."""

    def equities(step: int) -> list[str]:
        return [str(100000 + step * i) for i in range(1, days + 1)]

    baseline = metrics(equities(baseline_step), winning_trades(baseline_trades, "50"))
    treatment = metrics(equities(treatment_step), winning_trades(treatment_trades, "90"))
    return JevIncrementalAnalysis(PairedDayBootstrap(seed=1, resamples=100)).of(
        comparison(baseline, treatment, JevRunSummary(reviews=10, tokens=tokens)),
        _TRIALS,
        D("1"),
    )


def _classify(
    baseline: Verdict,
    treatment: Verdict | None,
    evidence: JevIncrementalEvidence,
) -> VerdictReport:
    return JevIncrementalPolicy.standard().classify(
        _verdict(baseline), None if treatment is None else _verdict(treatment), evidence
    )


def _codes(report: VerdictReport, outcome: GateOutcome) -> set[ReasonCode | None]:
    return {g.code for g in report.of(outcome)}


class TestAWeakBaseline:
    @pytest.mark.parametrize("baseline", [Verdict.REJECTED, Verdict.INCONCLUSIVE])
    def test_a_big_lift_on_a_weak_baseline_is_not_validated(self, baseline: Verdict) -> None:
        evidence = _evidence()  # a large, significant gain
        assert evidence.net_of_jev_average_trade_delta is not None
        assert evidence.net_of_jev_average_trade_delta > 0

        report = _classify(baseline, Verdict.VALIDATED, evidence)

        assert report.verdict is Verdict.INCONCLUSIVE
        assert ReasonCode.JEV_BASELINE_NOT_VALIDATED in _codes(report, GateOutcome.UNKNOWN)

    def test_it_is_rejected_when_the_jev_arm_is_itself_rejected(self) -> None:
        report = _classify(Verdict.REJECTED, Verdict.REJECTED, _evidence())

        assert report.verdict is Verdict.REJECTED
        assert ReasonCode.JEV_BASELINE_NOT_VALIDATED in _codes(report, GateOutcome.UNKNOWN)
        assert ReasonCode.JEV_TREATMENT_NOT_VALIDATED in _codes(report, GateOutcome.FAIL)

    def test_an_unjudged_jev_arm_is_inconclusive_not_rejected(self) -> None:
        assert _classify(Verdict.REJECTED, None, _evidence()).verdict is Verdict.INCONCLUSIVE

    def test_the_weak_branch_never_reads_the_evidence(self) -> None:
        worse = _evidence(treatment_step=50)  # Jev lost money: still only the baseline speaks

        report = _classify(Verdict.REJECTED, Verdict.INCONCLUSIVE, worse)

        assert report.verdict is Verdict.INCONCLUSIVE


class TestAValidatedBaseline:
    def test_a_real_significant_lift_with_no_worse_drawdown_is_validated(self) -> None:
        report = _classify(Verdict.VALIDATED, Verdict.VALIDATED, _evidence())

        assert report.verdict is Verdict.VALIDATED
        assert not report.of(GateOutcome.FAIL) and not report.of(GateOutcome.UNKNOWN)

    def test_the_jev_arm_must_pass_the_full_policy_itself(self) -> None:
        inconclusive = _classify(Verdict.VALIDATED, Verdict.INCONCLUSIVE, _evidence())
        rejected = _classify(Verdict.VALIDATED, Verdict.REJECTED, _evidence())
        unjudged = _classify(Verdict.VALIDATED, None, _evidence())

        assert inconclusive.verdict is Verdict.INCONCLUSIVE
        assert rejected.verdict is Verdict.REJECTED
        assert unjudged.verdict is Verdict.INCONCLUSIVE

    def test_a_gain_eaten_by_jevs_cost_is_rejected(self) -> None:
        costly = _evidence(tokens=10_000_000_000)

        report = _classify(Verdict.VALIDATED, Verdict.VALIDATED, costly)

        assert report.verdict is Verdict.REJECTED
        assert ReasonCode.JEV_NO_NET_EXPECTANCY_LIFT in _codes(report, GateOutcome.FAIL)

    def test_a_gain_whose_interval_straddles_zero_is_inconclusive(self) -> None:
        evidence = _evidence()
        assert evidence.paired is not None
        interval = replace(evidence.paired.mean_daily_return, low=D("-0.001"), high=D("0.001"))
        noisy = replace(evidence, paired=replace(evidence.paired, mean_daily_return=interval))

        report = _classify(Verdict.VALIDATED, Verdict.VALIDATED, noisy)

        assert report.verdict is Verdict.INCONCLUSIVE
        assert ReasonCode.JEV_LIFT_NOT_SIGNIFICANT in _codes(report, GateOutcome.UNKNOWN)

    def test_a_significantly_negative_interval_is_rejected(self) -> None:
        evidence = _evidence()
        assert evidence.paired is not None
        interval = replace(evidence.paired.mean_daily_return, low=D("-0.002"), high=D("-0.001"))
        harmed = replace(evidence, paired=replace(evidence.paired, mean_daily_return=interval))

        report = _classify(Verdict.VALIDATED, Verdict.VALIDATED, harmed)

        assert ReasonCode.JEV_LIFT_NOT_SIGNIFICANT in _codes(report, GateOutcome.FAIL)

    def test_unpairable_arms_leave_the_lift_unproven(self) -> None:
        short = _evidence(days=5)

        report = _classify(Verdict.VALIDATED, Verdict.VALIDATED, short)

        assert report.verdict is Verdict.INCONCLUSIVE
        assert ReasonCode.JEV_LIFT_NOT_SIGNIFICANT in _codes(report, GateOutcome.UNKNOWN)

    def test_a_worse_drawdown_is_rejected(self) -> None:
        evidence = _evidence()
        deltas = replace(evidence.deltas, max_drawdown=D("0.02"))

        report = _classify(Verdict.VALIDATED, Verdict.VALIDATED, replace(evidence, deltas=deltas))

        assert report.verdict is Verdict.REJECTED
        assert ReasonCode.JEV_DRAWDOWN_WORSE in _codes(report, GateOutcome.FAIL)

    def test_a_drawdown_within_the_declared_tolerance_passes(self) -> None:
        evidence = _evidence()
        deltas = replace(evidence.deltas, max_drawdown=D("0.02"))
        policy = JevIncrementalPolicy.standard(drawdown_tolerance=D("0.03"))

        report = policy.classify(
            _verdict(Verdict.VALIDATED),
            _verdict(Verdict.VALIDATED),
            replace(evidence, deltas=deltas),
        )

        assert report.verdict is Verdict.VALIDATED

    def test_an_arm_with_no_trades_leaves_the_expectancy_unknown(self) -> None:
        evidence = _evidence(treatment_trades=0)

        result = NetExpectancyLift().assess(
            _context(evidence, Verdict.VALIDATED, Verdict.VALIDATED)
        )

        assert result.outcome is GateOutcome.UNKNOWN


def _context(
    evidence: JevIncrementalEvidence, baseline: Verdict, treatment: Verdict
) -> JevGateContext:
    return JevGateContext(_verdict(baseline), _verdict(treatment), evidence)


def test_every_result_carries_a_reason_code() -> None:
    report = _classify(Verdict.VALIDATED, Verdict.VALIDATED, _evidence())

    assert all(g.code is not None for g in report.gates)


def test_a_negative_drawdown_tolerance_is_refused() -> None:
    with pytest.raises(ValueError, match="tolerance"):
        DrawdownNotWorse(D("-0.01"))


def test_a_policy_needs_gates_for_both_branches() -> None:
    with pytest.raises(ValueError, match="both branches"):
        JevIncrementalPolicy([], [SignificantLift()])
