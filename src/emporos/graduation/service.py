"""Promote, demote and retire a strategy configuration, and say where it stands.

A promotion resolves the current stage from the ledger, asks `GraduationGate` for the ORDER (one
stage at a time), runs every requirement of the target stage and reports every refusal together, or
appends a `GraduationEvent` that cites the evidence. A demotion is always allowed and always
recorded. The ledger is the single source of truth for the stage.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from emporos.core.clock import Clock
from emporos.domain.graduation import (
    EvidenceRef,
    GraduationEvent,
    GraduationStage,
    TransitionKind,
    effective_stage,
)
from emporos.graduation.policy import StagePolicy
from emporos.graduation.ports import GraduationLedger
from emporos.graduation.requirements import Assessment, PromotionRequest
from emporos.graduation.stage_mapping import StageMapping
from emporos.strategies.graduation import GraduationGate, GraduationStageError
from emporos.strategies.metadata import ValidationStatus


class PromotionRefused(Exception):
    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


class InvalidTransition(ValueError):
    """A demotion or retirement that does not make sense from where the strategy stands."""


@dataclass(frozen=True)
class RequirementReport:
    name: str
    assessment: Assessment


@dataclass(frozen=True)
class StandingReport:
    """Where a configuration stands and what the next stage still needs, requirement by
    requirement."""

    strategy: str
    behaviour_hash: str
    stage: GraduationStage
    next_stage: GraduationStage | None
    requirements: tuple[RequirementReport, ...]


class GraduationService:
    def __init__(
        self,
        ledger: GraduationLedger,
        policy: StagePolicy,
        clock: Clock,
        gate: GraduationGate | None = None,
    ) -> None:
        self._ledger = ledger
        self._policy = policy
        self._clock = clock
        self._gate = gate or GraduationGate()

    async def current(self, strategy: str, behaviour_hash: str) -> GraduationStage:
        return effective_stage(await self._ledger.latest(strategy), behaviour_hash)

    async def history(self, strategy: str) -> list[GraduationEvent]:
        return await self._ledger.history(strategy)

    async def standing(
        self, strategy: str, behaviour_hash: str, experiment_id: str | None = None,
        jev_enabled: bool = False,
    ) -> StandingReport:  # fmt: skip
        """Read-only: the stage, and how the NEXT stage's requirements stand right now."""
        stage = await self.current(strategy, behaviour_hash)
        target = _next_stage(stage)
        if target is None or not self._policy.promotable(target):
            return StandingReport(strategy, behaviour_hash, stage, None, ())
        request = PromotionRequest(
            strategy, behaviour_hash, stage, target, "status", experiment_id, jev_enabled
        )
        return StandingReport(
            strategy, behaviour_hash, stage, target, tuple(await self._assess(request))
        )

    async def promote(
        self, strategy: str, behaviour_hash: str, target: GraduationStage, actor: str,
        experiment_id: str | None = None, jev_enabled: bool = False,
    ) -> GraduationEvent:  # fmt: skip
        latest = await self._ledger.latest(strategy)
        stage = effective_stage(latest, behaviour_hash)
        self._require_next_stage(stage, target)
        if not self._policy.promotable(target):
            raise PromotionRefused([f"{target.value} is not promotable"])
        request = PromotionRequest(
            strategy, behaviour_hash, stage, target, actor, experiment_id, jev_enabled
        )
        reports = await self._assess(request)
        reasons = [r.assessment.refusal for r in reports if r.assessment.refusal is not None]
        if reasons:
            raise PromotionRefused(reasons)
        evidence = tuple(
            r.assessment.evidence for r in reports if r.assessment.evidence is not None
        )
        if not evidence:
            raise PromotionRefused(["no requirement cited any evidence for this promotion"])
        return await self._append(
            latest, strategy, behaviour_hash, stage, target, TransitionKind.PROMOTE, evidence,
            actor, f"promoted to {target.value}",
        )  # fmt: skip

    async def demote(
        self, strategy: str, behaviour_hash: str, to: GraduationStage, actor: str, reason: str
    ) -> GraduationEvent:
        latest = await self._ledger.latest(strategy)
        stage = effective_stage(latest, behaviour_hash)
        if stage is GraduationStage.RETIRED or to is GraduationStage.RETIRED:
            raise InvalidTransition("use retire, not demote, to take a strategy off the road")
        if to.rank >= stage.rank:
            raise InvalidTransition(
                f"{strategy} is at {stage.value}: it cannot be demoted to {to.value}"
            )
        return await self._append(
            latest, strategy, behaviour_hash, stage, to, TransitionKind.DEMOTE, (), actor, reason
        )

    async def retire(
        self, strategy: str, behaviour_hash: str, actor: str, reason: str
    ) -> GraduationEvent:
        latest = await self._ledger.latest(strategy)
        stage = effective_stage(latest, behaviour_hash)
        if stage is GraduationStage.RETIRED:
            raise InvalidTransition(f"{strategy} is already retired")
        return await self._append(
            latest, strategy, behaviour_hash, stage, GraduationStage.RETIRED,
            TransitionKind.RETIRE, (), actor, reason,
        )  # fmt: skip

    # --- internals -----------------------------------------------------------------------------
    def _require_next_stage(self, stage: GraduationStage, target: GraduationStage) -> None:
        """The ORDER, from `GraduationGate`. Whether the evidence is there is the requirements'
        job (every promotable stage lists `ValidatedVerdictForConfig`), so the gate's own
        validation argument is not repeated here."""
        if stage is GraduationStage.RETIRED:
            raise PromotionRefused(["a retired strategy cannot be promoted"])
        try:
            self._gate.advance_deployment(
                ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
                StageMapping.to_deployment(stage),
                StageMapping.to_deployment(target),
            )
        except GraduationStageError as error:
            raise PromotionRefused([str(error)]) from error

    async def _assess(self, request: PromotionRequest) -> list[RequirementReport]:
        reports: list[RequirementReport] = []
        for requirement in self._policy.requirements_for(request.target):
            try:
                assessment = await requirement.assess(request)
            except Exception as error:
                # A requirement that cannot answer has not been met.
                assessment = Assessment.refused(
                    f"{requirement.name} could not be checked: {error!r}"
                )
            reports.append(RequirementReport(requirement.name, assessment))
        return reports

    async def _append(
        self, latest: GraduationEvent | None, strategy: str, behaviour_hash: str,
        origin: GraduationStage, target: GraduationStage, kind: TransitionKind,
        evidence: tuple[EvidenceRef, ...], actor: str, reason: str,
    ) -> GraduationEvent:  # fmt: skip
        event = GraduationEvent(
            strategy, behaviour_hash, 1 if latest is None else latest.seq + 1, origin, target,
            kind, evidence, actor, reason, self._clock.now(),
        )  # fmt: skip
        await self._ledger.append(event)
        return event


def _next_stage(stage: GraduationStage) -> GraduationStage | None:
    order = (
        GraduationStage.RESEARCH,
        GraduationStage.PAPER,
        GraduationStage.LIVE_CONSERVATIVE,
        GraduationStage.PRODUCTION,
    )
    if stage not in order or stage is order[-1]:
        return None
    return order[order.index(stage) + 1]
