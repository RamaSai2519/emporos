"""One test group per promotion requirement, each with the evidence it reads faked (EM-189).

Every requirement fails closed: missing, unreadable, stale and negative evidence all refuse."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.broker_verification import BrokerCheck, CheckOutcome
from emporos.domain.experiments import Verdict
from emporos.domain.graduation import (
    EvidenceKind,
    GraduationStage,
    LiveAcknowledgement,
    acknowledgement_phrase,
)
from emporos.domain.research_experiments import ExperimentOutcomeLabel
from emporos.domain.verdicts import GateFinding
from emporos.graduation.requirements import (
    BrokerVerificationPassed,
    DataIntegrityClean,
    HoldoutEvaluated,
    JevDependencyAllowed,
    LiveAcknowledged,
    NoOpenAnomalies,
    PaperReconciliationPassed,
    PromotionRequest,
    ValidatedVerdictForConfig,
)
from tests.support.graduation import (
    HASH,
    NOW,
    STRATEGY,
    FakeBrokerEvidence,
    FakeExperiments,
    FakeParity,
    FakeQuarantine,
    FakeSwitch,
    FakeUnresolved,
    FakeVerdictBook,
    MemoryAcknowledgements,
    all_passing,
    experiment_view,
    parity_report,
    recorded,
)

EXP = "EXP-20260924-orb-v1-abcd1234"


def request(**changes: object) -> PromotionRequest:
    base = PromotionRequest(
        STRATEGY, HASH, GraduationStage.RESEARCH, GraduationStage.PAPER, "rama", EXP
    )
    return replace(base, **changes)  # type: ignore[arg-type]


class TestValidatedVerdictForConfig:
    async def test_a_validated_verdict_for_this_config_is_met_and_cited(self) -> None:
        got = await ValidatedVerdictForConfig(FakeVerdictBook(recorded())).assess(request())
        assert got.is_met and got.evidence is not None and got.evidence.kind is EvidenceKind.VERDICT

    @pytest.mark.parametrize(
        ("book", "phrase"),
        [
            (FakeVerdictBook(None), "no recorded verdict"),
            (FakeVerdictBook(recorded(behaviour_hash="0" * 16)), "different configuration"),
            (FakeVerdictBook(recorded(Verdict.REJECTED)), "rejected"),
            (FakeVerdictBook(recorded(Verdict.INCONCLUSIVE)), "inconclusive"),
        ],
    )
    async def test_anything_else_is_refused(self, book: FakeVerdictBook, phrase: str) -> None:
        got = await ValidatedVerdictForConfig(book).assess(request())
        assert got.refusal is not None and phrase in got.refusal


class TestHoldoutEvaluated:
    async def test_a_cited_report_with_a_settled_holdout_is_met(self) -> None:
        got = await HoldoutEvaluated(FakeExperiments(experiment_view())).assess(request())
        assert got.is_met and got.evidence is not None and got.evidence.ref == EXP

    @pytest.mark.parametrize(
        ("experiments", "req", "phrase"),
        [
            (FakeExperiments(experiment_view()), request(experiment_id=None), "no experiment"),
            (FakeExperiments(), request(), "not a published report"),
            (
                FakeExperiments(experiment_view(hashes=frozenset({"0" * 16}))),
                request(),
                "not run on this configuration",
            ),
            (FakeExperiments(experiment_view(holdout=False)), request(), "no holdout"),
            (
                FakeExperiments(experiment_view(unsettled=frozenset({"holdout_not_evaluated"}))),
                request(),
                "holdout_not_evaluated",
            ),
        ],
    )
    async def test_missing_or_unsettled_evidence_is_refused(
        self, experiments: FakeExperiments, req: PromotionRequest, phrase: str
    ) -> None:
        got = await HoldoutEvaluated(experiments).assess(req)
        assert got.refusal is not None and phrase in got.refusal

    async def test_an_unrelated_unsettled_reason_does_not_matter_here(self) -> None:
        view = experiment_view(unsettled=frozenset({"too_few_trades"}))
        assert (await HoldoutEvaluated(FakeExperiments(view)).assess(request())).is_met


class TestDataIntegrityClean:
    def rule(self, view, quarantine: str = "q-hash", allowed=frozenset()):  # type: ignore[no-untyped-def]
        return DataIntegrityClean(FakeExperiments(view), FakeQuarantine(quarantine), allowed)

    async def test_clean_provenance_under_the_current_quarantine_is_met(self) -> None:
        assert (await self.rule(experiment_view()).assess(request())).is_met

    async def test_an_allowlisted_assumed_instrument_is_tolerated_and_another_is_not(self) -> None:
        view = experiment_view(assumed=("NSE:1",))
        assert (await self.rule(view, allowed=frozenset({"NSE:1"})).assess(request())).is_met
        refused = await self.rule(view).assess(request())
        assert refused.refusal is not None and "NSE:1" in refused.refusal

    @pytest.mark.parametrize("missing", [{"assumed": None}, {"quarantine": None}])
    async def test_unrecorded_provenance_is_refused_not_assumed_clean(self, missing) -> None:  # type: ignore[no-untyped-def]
        got = await self.rule(experiment_view(**missing)).assess(request())
        assert got.refusal is not None and "did not record" in got.refusal

    async def test_a_quarantine_that_changed_since_the_run_is_refused(self) -> None:
        got = await self.rule(experiment_view(), quarantine="other").assess(request())
        assert got.refusal is not None and "quarantine changed" in got.refusal

    async def test_an_uncited_report_is_refused(self) -> None:
        got = await self.rule(experiment_view()).assess(request(experiment_id=None))
        assert got.refusal is not None and "no experiment" in got.refusal


class TestPaperReconciliationPassed:
    async def test_a_validated_report_with_enough_sessions_is_met(self) -> None:
        rule = PaperReconciliationPassed(FakeParity(parity_report()), min_sessions=10)
        got = await rule.assess(request())
        assert got.is_met and got.evidence is not None
        assert got.evidence.kind is EvidenceKind.RECONCILIATION_REPORT

    async def test_no_report_is_refused(self) -> None:
        got = await PaperReconciliationPassed(FakeParity(None), 10).assess(request())
        assert got.refusal is not None and "no paper-vs-backtest" in got.refusal

    async def test_too_few_sessions_are_refused_even_when_validated(self) -> None:
        got = await PaperReconciliationPassed(FakeParity(parity_report(sessions=3)), 10).assess(
            request()
        )
        assert got.refusal is not None and "only 3 paper session" in got.refusal

    @pytest.mark.parametrize("verdict", [Verdict.INCONCLUSIVE, Verdict.REJECTED])
    async def test_a_report_that_is_not_validated_lists_its_gates(self, verdict: Verdict) -> None:
        gates = (
            GateFinding("fill rate", "fail", "0.5 vs 0.9"),
            GateFinding("slippage", "unknown", "too few trades"),
        )
        report = parity_report(verdict, gates=gates)
        got = await PaperReconciliationPassed(FakeParity(report), 10).assess(request())
        assert got.refusal is not None
        assert verdict.value in got.refusal and "fill rate: fail" in got.refusal
        assert "slippage: unknown" in got.refusal

    def test_at_least_one_session_must_be_required(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            PaperReconciliationPassed(FakeParity(), 0)


class TestBrokerVerificationPassed:
    def rule(self, checks):  # type: ignore[no-untyped-def]
        return BrokerVerificationPassed(
            FakeBrokerEvidence(checks), FixedClock(NOW), timedelta(days=7)
        )

    async def test_every_critical_check_passing_and_recent_is_met(self) -> None:
        assert (await self.rule(all_passing()).assess(request())).is_met

    async def test_no_evidence_at_all_is_refused(self) -> None:
        got = await self.rule([]).assess(request())
        assert got.refusal is not None and "no broker verification evidence" in got.refusal

    async def test_a_failed_and_an_unknown_check_are_both_named(self) -> None:
        checks = all_passing()
        checks[0] = BrokerCheck("login_and_session", CheckOutcome.FAIL, NOW)
        checks[1] = BrokerCheck("static_ip_registered", CheckOutcome.UNKNOWN, None)
        got = await self.rule(checks).assess(request())
        assert got.refusal is not None
        assert "login_and_session: fail" in got.refusal
        assert "static_ip_registered: unknown" in got.refusal

    async def test_a_pass_older_than_the_allowance_is_stale(self) -> None:
        old = all_passing(NOW - timedelta(days=8))
        got = await self.rule(old).assess(request())
        assert got.refusal is not None and "stale" in got.refusal

    async def test_a_pass_with_no_time_is_not_trusted(self) -> None:
        checks = [BrokerCheck("login_and_session", CheckOutcome.PASS, None)]
        got = await self.rule(checks).assess(request())
        assert got.refusal is not None and "stale" in got.refusal

    def test_the_allowance_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            BrokerVerificationPassed(FakeBrokerEvidence(), FixedClock(NOW), timedelta(0))


class TestLiveAcknowledged:
    def acknowledgement(self) -> LiveAcknowledgement:
        return LiveAcknowledgement(
            STRATEGY, HASH, "rama", acknowledgement_phrase(STRATEGY, HASH), "live_conservative", NOW
        )

    async def test_an_acknowledgement_for_this_config_is_met_and_cited(self) -> None:
        book = MemoryAcknowledgements(self.acknowledgement())
        got = await LiveAcknowledged(book).assess(request())
        assert got.is_met and got.evidence is not None
        assert got.evidence.kind is EvidenceKind.ACKNOWLEDGEMENT

    async def test_none_is_refused_with_the_exact_phrase_to_type(self) -> None:
        got = await LiveAcknowledged(MemoryAcknowledgements()).assess(request())
        assert got.refusal is not None and "orb_v1@abcdef01 LIVE" in got.refusal

    async def test_an_acknowledgement_for_another_config_does_not_count(self) -> None:
        other = replace(
            self.acknowledgement(), behaviour_hash="1" * 16,
            typed_phrase=acknowledgement_phrase(STRATEGY, "1" * 16),
        )  # fmt: skip
        got = await LiveAcknowledged(MemoryAcknowledgements(other)).assess(request())
        assert not got.is_met


class TestNoOpenAnomalies:
    async def test_clear_switch_and_no_unresolved_orders_is_met(self) -> None:
        assert (
            await NoOpenAnomalies(FakeSwitch(False), FakeUnresolved(0)).assess(request())
        ).is_met

    async def test_an_engaged_switch_and_unresolved_orders_are_both_reported(self) -> None:
        got = await NoOpenAnomalies(FakeSwitch(True), FakeUnresolved(2)).assess(request())
        assert got.refusal is not None
        assert "kill switch" in got.refusal and "2 order(s)" in got.refusal


class TestJevDependencyAllowed:
    def jev(self, outcome: ExperimentOutcomeLabel = ExperimentOutcomeLabel.ACCEPTED, **kw):  # type: ignore[no-untyped-def]
        return experiment_view("EXP-20260924-jev-abcd1234", "jev_incremental", outcome, **kw)

    async def test_a_deployment_without_jev_is_untouched(self) -> None:
        got = await JevDependencyAllowed(FakeExperiments()).assess(request(jev_enabled=False))
        assert got.is_met and got.evidence is None

    async def test_jev_enabled_with_no_experiment_is_refused(self) -> None:
        got = await JevDependencyAllowed(FakeExperiments()).assess(request(jev_enabled=True))
        assert got.refusal is not None and "no jev_incremental experiment" in got.refusal

    @pytest.mark.parametrize(
        "outcome", [ExperimentOutcomeLabel.INCONCLUSIVE, ExperimentOutcomeLabel.REJECTED]
    )
    async def test_jev_enabled_with_an_unaccepted_experiment_is_refused(
        self, outcome: ExperimentOutcomeLabel
    ) -> None:
        rule = JevDependencyAllowed(FakeExperiments(self.jev(outcome)))
        got = await rule.assess(request(jev_enabled=True))
        assert got.refusal is not None and outcome.value in got.refusal

    async def test_jev_enabled_with_an_accepted_experiment_for_this_config_is_met(self) -> None:
        rule = JevDependencyAllowed(FakeExperiments(self.jev()))
        got = await rule.assess(request(jev_enabled=True))
        assert got.is_met and got.evidence is not None
        assert got.evidence.kind is EvidenceKind.JEV_EXPERIMENT

    async def test_an_accepted_experiment_for_another_config_does_not_count(self) -> None:
        rule = JevDependencyAllowed(FakeExperiments(self.jev(hashes=frozenset({"0" * 16}))))
        assert not (await rule.assess(request(jev_enabled=True))).is_met

    async def test_only_the_newest_experiment_counts(self) -> None:
        old = replace(self.jev(), declared_at=NOW - timedelta(days=5))
        new = replace(
            self.jev(ExperimentOutcomeLabel.REJECTED), experiment_id="EXP-20260924-jev-ffff1111"
        )
        got = await JevDependencyAllowed(FakeExperiments(old, new)).assess(
            request(jev_enabled=True)
        )
        assert got.refusal is not None and "rejected" in got.refusal
