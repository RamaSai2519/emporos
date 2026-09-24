"""The graduation service: ordering, every refusal at once, the ledger as the source of truth."""

from __future__ import annotations

from datetime import timedelta

import pytest

from emporos.core.clock import FixedClock
from emporos.domain.graduation import (
    EvidenceKind,
    EvidenceRef,
    GraduationStage,
    LiveAcknowledgement,
    TransitionKind,
    acknowledgement_phrase,
)
from emporos.graduation.policy import StagePolicy, standard_policy
from emporos.graduation.requirements import (
    Assessment,
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
from emporos.graduation.service import GraduationService, InvalidTransition, PromotionRefused
from emporos.graduation.stage_mapping import StageMapping
from emporos.persistence.graduation_store import GraduationConflictError
from emporos.strategies.metadata import DeploymentStatus
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
    MemoryLedger,
    experiment_view,
    parity_report,
    recorded,
)

S = GraduationStage
EXP = "EXP-20260924-orb-v1-abcd1234"


class Scripted:
    """A requirement that says what it is told."""

    def __init__(self, name: str, refusal: str | None = None, boom: bool = False) -> None:
        self.name, self._refusal, self._boom = name, refusal, boom

    async def assess(self, request: PromotionRequest) -> Assessment:
        if self._boom:
            raise RuntimeError("evidence store down")
        if self._refusal:
            return Assessment.refused(self._refusal)
        return Assessment.met(EvidenceRef(EvidenceKind.EXPERIMENT, f"ref-{self.name}"))


def service(ledger: MemoryLedger | None = None, policy: StagePolicy | None = None):  # type: ignore[no-untyped-def]
    ledger = ledger if ledger is not None else MemoryLedger()
    policy = policy or StagePolicy(
        {S.PAPER: [Scripted("a")], S.LIVE_CONSERVATIVE: [Scripted("a"), Scripted("b")]}
    )
    return GraduationService(ledger, policy, FixedClock(NOW)), ledger


class TestPromotion:
    async def test_a_new_configuration_starts_in_research(self) -> None:
        svc, _ = service()
        assert await svc.current(STRATEGY, HASH) is S.RESEARCH

    async def test_promotion_appends_an_event_and_moves_the_stage(self) -> None:
        svc, ledger = service()

        event = await svc.promote(STRATEGY, HASH, S.PAPER, "rama")

        assert (event.seq, event.from_stage, event.to_stage) == (1, S.RESEARCH, S.PAPER)
        assert event.kind is TransitionKind.PROMOTE and event.at == NOW
        assert ledger.events == [event]
        assert await svc.current(STRATEGY, HASH) is S.PAPER

    async def test_the_sequence_is_monotonic_across_promotions(self) -> None:
        svc, _ = service()
        await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        second = await svc.promote(STRATEGY, HASH, S.LIVE_CONSERVATIVE, "rama")
        assert second.seq == 2 and second.from_stage is S.PAPER

    async def test_stages_cannot_be_skipped(self) -> None:
        svc, ledger = service()
        with pytest.raises(PromotionRefused, match="one stage at a time"):
            await svc.promote(STRATEGY, HASH, S.LIVE_CONSERVATIVE, "rama")
        assert ledger.events == []

    async def test_production_is_not_promotable_even_from_live(self) -> None:
        svc, _ = service()
        await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        await svc.promote(STRATEGY, HASH, S.LIVE_CONSERVATIVE, "rama")
        with pytest.raises(PromotionRefused, match="production is not promotable"):
            await svc.promote(STRATEGY, HASH, S.PRODUCTION, "rama")

    async def test_every_refusal_is_reported_together_and_nothing_is_recorded(self) -> None:
        policy = StagePolicy(
            {S.PAPER: [Scripted("a", "first"), Scripted("b"), Scripted("c", "third")]}
        )
        svc, ledger = service(policy=policy)
        with pytest.raises(PromotionRefused) as refused:
            await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        assert refused.value.reasons == ("first", "third")
        assert ledger.events == []

    async def test_two_requirements_refusing_for_one_cause_say_it_once(self) -> None:
        policy = StagePolicy({S.PAPER: [Scripted("a", "same"), Scripted("b", "same")]})
        svc, _ = service(policy=policy)
        with pytest.raises(PromotionRefused) as refused:
            await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        assert refused.value.reasons == ("same",)

    async def test_a_requirement_that_cannot_answer_counts_as_refusing(self) -> None:
        svc, ledger = service(policy=StagePolicy({S.PAPER: [Scripted("flaky", boom=True)]}))
        with pytest.raises(PromotionRefused, match="flaky could not be checked"):
            await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        assert ledger.events == []

    async def test_the_event_cites_the_evidence_of_the_requirements_that_gave_any(self) -> None:
        book = FakeVerdictBook(recorded())
        svc, _ = service(policy=StagePolicy({S.PAPER: [ValidatedVerdictForConfig(book)]}))
        event = await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        assert [e.kind for e in event.evidence] == [EvidenceKind.VERDICT]

    async def test_an_edited_configuration_starts_again_from_research(self) -> None:
        svc, _ = service()
        await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        assert await svc.current(STRATEGY, "0" * 16) is S.RESEARCH
        with pytest.raises(PromotionRefused, match="one stage at a time"):
            await svc.promote(STRATEGY, "0" * 16, S.LIVE_CONSERVATIVE, "rama")

    async def test_a_lost_race_for_a_sequence_number_surfaces_as_a_conflict(self) -> None:
        class ReadsBeforeTheRival(MemoryLedger):
            async def latest(self, strategy: str):  # type: ignore[no-untyped-def]
                return None  # this process read the ledger before the other one wrote

        ledger = ReadsBeforeTheRival()
        await service(ledger)[0].promote(STRATEGY, HASH, S.PAPER, "other")

        with pytest.raises(GraduationConflictError):
            await service(ledger)[0].promote(STRATEGY, HASH, S.PAPER, "rama")
        assert len(ledger.events) == 1

    async def test_a_promotion_no_requirement_can_cite_evidence_for_is_refused(self) -> None:
        class Silent:
            name = "silent"

            async def assess(self, request: PromotionRequest) -> Assessment:
                return Assessment.met()

        svc, ledger = service(policy=StagePolicy({S.PAPER: [Silent()]}))
        with pytest.raises(PromotionRefused, match="cited any evidence"):
            await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        assert ledger.events == []


class TestDemotionAndRetirement:
    async def test_a_demotion_is_always_allowed_and_recorded_with_its_reason(self) -> None:
        svc, ledger = service()
        await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        await svc.promote(STRATEGY, HASH, S.LIVE_CONSERVATIVE, "rama")

        event = await svc.demote(STRATEGY, HASH, S.RESEARCH, "rama", "drawdown breach")

        assert (event.kind, event.to_stage, event.reason) == (
            TransitionKind.DEMOTE, S.RESEARCH, "drawdown breach"
        )  # fmt: skip
        assert event.seq == 3 and await svc.current(STRATEGY, HASH) is S.RESEARCH
        assert len(ledger.events) == 3

    async def test_a_demotion_that_does_not_go_down_is_refused(self) -> None:
        svc, _ = service()
        with pytest.raises(InvalidTransition, match="cannot be demoted"):
            await svc.demote(STRATEGY, HASH, S.PAPER, "rama", "x")

    async def test_retirement_is_final_and_beats_any_configuration(self) -> None:
        svc, _ = service()
        await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        await svc.retire(STRATEGY, HASH, "rama", "no edge")

        assert await svc.current(STRATEGY, HASH) is S.RETIRED
        assert await svc.current(STRATEGY, "0" * 16) is S.RETIRED
        with pytest.raises(PromotionRefused, match="retired"):
            await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        with pytest.raises(InvalidTransition):
            await svc.retire(STRATEGY, HASH, "rama", "again")
        with pytest.raises(InvalidTransition, match="use retire"):
            await svc.demote(STRATEGY, HASH, S.RESEARCH, "rama", "x")

    async def test_history_lists_every_event_in_order(self) -> None:
        svc, _ = service()
        await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        await svc.demote(STRATEGY, HASH, S.RESEARCH, "rama", "x")
        assert [e.seq for e in await svc.history(STRATEGY)] == [1, 2]


class TestStanding:
    async def test_it_shows_each_requirement_of_the_next_stage_without_writing(self) -> None:
        policy = StagePolicy({S.PAPER: [Scripted("a"), Scripted("b", "missing b")]})
        svc, ledger = service(policy=policy)

        report = await svc.standing(STRATEGY, HASH)

        assert (report.stage, report.next_stage) == (S.RESEARCH, S.PAPER)
        assert [(r.name, r.assessment.is_met) for r in report.requirements] == [
            ("a", True), ("b", False)
        ]  # fmt: skip
        assert ledger.events == []

    async def test_a_stage_with_no_policy_has_no_next_requirements(self) -> None:
        svc, _ = service()
        await svc.promote(STRATEGY, HASH, S.PAPER, "rama")
        await svc.promote(STRATEGY, HASH, S.LIVE_CONSERVATIVE, "rama")
        report = await svc.standing(STRATEGY, HASH)
        assert report.next_stage is None and report.requirements == ()


class TestStandardPolicy:
    def policy(self) -> StagePolicy:
        experiments = FakeExperiments(experiment_view())
        paper = [
            ValidatedVerdictForConfig(FakeVerdictBook(recorded())),
            HoldoutEvaluated(experiments),
            DataIntegrityClean(experiments, FakeQuarantine()),
        ]
        live = [
            PaperReconciliationPassed(FakeParity(parity_report()), 10),
            BrokerVerificationPassed(
                FakeBrokerEvidence(), FixedClock(NOW), timedelta(days=7)
            ),
            LiveAcknowledged(
                MemoryAcknowledgements(
                    LiveAcknowledgement(
                        STRATEGY, HASH, "rama", acknowledgement_phrase(STRATEGY, HASH),
                        "live_conservative", NOW,
                    )
                )
            ),
            NoOpenAnomalies(FakeSwitch(False), FakeUnresolved(0)),
            JevDependencyAllowed(experiments),
        ]  # fmt: skip
        return standard_policy(paper, live)

    async def test_live_demands_every_paper_requirement_as_well(self) -> None:
        policy = self.policy()
        paper = [r.name for r in policy.requirements_for(S.PAPER)]
        live = [r.name for r in policy.requirements_for(S.LIVE_CONSERVATIVE)]
        assert paper == [
            "validated_verdict_for_config",
            "holdout_evaluated",
            "data_integrity_clean",
        ]
        assert live[: len(paper)] == paper and len(live) == len(paper) + 5

    def test_only_paper_and_live_conservative_are_promotable(self) -> None:
        policy = self.policy()
        assert policy.promotable(S.PAPER) and policy.promotable(S.LIVE_CONSERVATIVE)
        assert not policy.promotable(S.PRODUCTION)
        assert not policy.promotable(S.RESEARCH) and not policy.promotable(S.RETIRED)
        assert policy.requirements_for(S.PRODUCTION) == ()

    async def test_with_all_evidence_green_the_whole_road_can_be_walked(self) -> None:
        svc = GraduationService(MemoryLedger(), self.policy(), FixedClock(NOW))
        await svc.promote(STRATEGY, HASH, S.PAPER, "rama", experiment_id=EXP)
        live = await svc.promote(STRATEGY, HASH, S.LIVE_CONSERVATIVE, "rama", experiment_id=EXP)
        kinds = {e.kind for e in live.evidence}
        assert {
            EvidenceKind.VERDICT, EvidenceKind.EXPERIMENT, EvidenceKind.RECONCILIATION_REPORT,
            EvidenceKind.BROKER_VERIFICATION, EvidenceKind.ACKNOWLEDGEMENT,
        } <= kinds  # fmt: skip

    async def test_a_policy_with_an_empty_stage_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="no requirements"):
            StagePolicy({S.PAPER: []})


class TestStageMapping:
    @pytest.mark.parametrize("stage", list(GraduationStage))
    def test_every_stage_round_trips_through_the_deployment_status(
        self, stage: GraduationStage
    ) -> None:
        assert StageMapping.from_deployment(StageMapping.to_deployment(stage)) is stage

    def test_research_is_the_candidate_deployment(self) -> None:
        assert StageMapping.to_deployment(S.RESEARCH) is DeploymentStatus.CANDIDATE
