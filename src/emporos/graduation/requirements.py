"""One class per promotion requirement (Open/Closed): a new rule is a new class in a stage's list.

Every requirement FAILS CLOSED. Missing evidence, unreadable evidence and stale evidence are all
refusals with a reason, never a pass; a requirement that cannot decide says so. They are evaluated
together and every refusal is reported, so an operator fixes the lot in one pass (the same shape as
`session.launch_gate.LaunchCondition`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from emporos.core.clock import Clock
from emporos.domain.broker_verification import BrokerVerificationEvidence, CheckOutcome
from emporos.domain.experiments import Verdict
from emporos.domain.graduation import (
    EvidenceKind,
    EvidenceRef,
    GraduationStage,
    acknowledgement_phrase,
)
from emporos.domain.parity import PaperReconciliationEvidence
from emporos.domain.research_experiments import ExperimentFamily, ExperimentOutcomeLabel, ReasonCode
from emporos.domain.verdicts import Standing, standing_of
from emporos.graduation.ports import (
    AcknowledgementBook,
    CurrentQuarantine,
    ExperimentEvidence,
    ExperimentEvidenceView,
    UnresolvedOrders,
)
from emporos.session.launch_gate import SwitchView, VerdictBook


@dataclass(frozen=True)
class PromotionRequest:
    strategy: str
    behaviour_hash: str
    current: GraduationStage
    target: GraduationStage
    actor: str
    experiment_id: str | None = None  # the EM-188 report the operator cites as evidence
    jev_enabled: bool = False  # whether this deployment runs the strategy with Jev in the loop


@dataclass(frozen=True)
class Assessment:
    """A requirement's answer: refused with a reason, or met (optionally citing its evidence)."""

    refusal: str | None = None
    evidence: EvidenceRef | None = None

    @classmethod
    def met(cls, evidence: EvidenceRef | None = None) -> Assessment:
        return cls(None, evidence)

    @classmethod
    def refused(cls, reason: str) -> Assessment:
        if not reason.strip():
            raise ValueError("a refusal must say why")
        return cls(reason, None)

    @property
    def is_met(self) -> bool:
        return self.refusal is None


class PromotionRequirement(Protocol):
    name: str

    async def assess(self, request: PromotionRequest) -> Assessment: ...


class ValidatedVerdictForConfig:
    """The latest recorded verdict is VALIDATED for exactly this behaviour hash. That verdict is
    the robustness gates' conclusion: cost, PBO, DSR, regimes, walk-forward windows, portfolio
    economics."""

    name = "validated_verdict_for_config"

    def __init__(self, book: VerdictBook) -> None:
        self._book = book

    async def assess(self, request: PromotionRequest) -> Assessment:
        verdict = await self._book.latest(request.strategy)
        standing = standing_of(verdict, request.behaviour_hash)
        if standing is Standing.VALIDATED and verdict is not None:
            return Assessment.met(
                EvidenceRef(EvidenceKind.VERDICT, verdict.experiment, "validated")
            )
        if standing is Standing.STALE:
            return Assessment.refused(
                "the verdict on record was recorded for a different configuration"
            )
        if standing is Standing.NONE:
            return Assessment.refused(f"{request.strategy} has no recorded verdict")
        return Assessment.refused(f"{request.strategy} is {standing.value}, not validated")


async def _cited_experiment(
    request: PromotionRequest, experiments: ExperimentEvidence
) -> ExperimentEvidenceView | str:
    """The cited report if it exists and was run on this configuration, else why not."""
    if request.experiment_id is None:
        return "no experiment report is cited: name the EM-188 report that is the evidence"
    view = await experiments.get(request.experiment_id)
    if view is None:
        return f"experiment {request.experiment_id} is not a published report"
    if request.behaviour_hash not in view.behaviour_hashes:
        return f"experiment {request.experiment_id} was not run on this configuration"
    return view


class HoldoutEvaluated:
    """The cited report reserved a holdout and neither holdout reason is outstanding."""

    name = "holdout_evaluated"
    _HOLDOUT_CODES = frozenset(
        {ReasonCode.HOLDOUT_NOT_RESERVED.value, ReasonCode.HOLDOUT_NOT_EVALUATED.value}
    )

    def __init__(self, experiments: ExperimentEvidence) -> None:
        self._experiments = experiments

    async def assess(self, request: PromotionRequest) -> Assessment:
        cited = await _cited_experiment(request, self._experiments)
        if isinstance(cited, str):
            return Assessment.refused(cited)
        if not cited.holdout_reserved:
            return Assessment.refused(f"{cited.experiment_id} reserved no holdout period")
        outstanding = sorted(cited.unsettled_reason_codes & self._HOLDOUT_CODES)
        if outstanding:
            return Assessment.refused(
                f"{cited.experiment_id} holdout is not settled: {', '.join(outstanding)}"
            )
        return Assessment.met(EvidenceRef(EvidenceKind.EXPERIMENT, cited.experiment_id, "holdout"))


class DataIntegrityClean:
    """The cited report recorded its data provenance: nothing outside the allowlist was assumed
    into its universe, and the quarantine it ran under is the one in force now."""

    name = "data_integrity_clean"

    def __init__(
        self,
        experiments: ExperimentEvidence,
        quarantine: CurrentQuarantine,
        allowed_assumed_instruments: frozenset[str] = frozenset(),
    ) -> None:
        self._experiments = experiments
        self._quarantine = quarantine
        self._allowed = allowed_assumed_instruments

    async def assess(self, request: PromotionRequest) -> Assessment:
        cited = await _cited_experiment(request, self._experiments)
        if isinstance(cited, str):
            return Assessment.refused(cited)
        if cited.assumed_instrument_ids is None or cited.quarantine_hash is None:
            return Assessment.refused(
                f"{cited.experiment_id} did not record its data provenance, so it cannot be "
                "shown to have run on clean data"
            )
        assumed = sorted(set(cited.assumed_instrument_ids) - self._allowed)
        if assumed:
            names = ", ".join(assumed)
            return Assessment.refused(
                f"{cited.experiment_id} assumed instruments it could not verify: {names}"
            )
        current = await self._quarantine.hash()
        if cited.quarantine_hash != current:
            return Assessment.refused(
                f"the corporate-action quarantine changed since {cited.experiment_id} ran"
            )
        return Assessment.met(
            EvidenceRef(EvidenceKind.EXPERIMENT, cited.experiment_id, "data integrity")
        )


class PaperReconciliationPassed:
    """The newest cumulative paper-vs-backtest report for this configuration is VALIDATED, with at
    least `min_sessions` paper sessions behind it (EM-185)."""

    name = "paper_reconciliation_passed"

    def __init__(self, evidence: PaperReconciliationEvidence, min_sessions: int) -> None:
        if min_sessions < 1:
            raise ValueError("at least one paper session must be required")
        self._evidence = evidence
        self._min_sessions = min_sessions

    async def assess(self, request: PromotionRequest) -> Assessment:
        report = await self._evidence.latest(request.strategy, request.behaviour_hash)
        if report is None:
            return Assessment.refused(
                f"no paper-vs-backtest reconciliation report exists for {request.strategy} "
                "in this configuration"
            )
        if report.sessions < self._min_sessions:
            return Assessment.refused(
                f"only {report.sessions} paper session(s) reconciled, {self._min_sessions} needed"
            )
        if report.verdict is not Verdict.VALIDATED:
            findings = "; ".join(f"{g.name}: {g.outcome}" for g in report.failing)
            unresolved = "; ".join(
                f"{g.name}: {g.outcome}" for g in report.gates if g.outcome == "unknown"
            )
            detail = "; ".join(x for x in (findings, unresolved) if x)
            return Assessment.refused(
                f"paper reconciliation is {report.verdict.value} ({report.period})"
                + (f": {detail}" if detail else "")
            )
        ref = f"{request.strategy}:{request.behaviour_hash[:8]}:{report.period}"
        return Assessment.met(EvidenceRef(EvidenceKind.RECONCILIATION_REPORT, ref))


class BrokerVerificationPassed:
    """Every critical broker check is a PASS that is not older than `max_age`."""

    name = "broker_verification_passed"

    def __init__(
        self, evidence: BrokerVerificationEvidence, clock: Clock, max_age: timedelta
    ) -> None:
        if max_age <= timedelta(0):
            raise ValueError("broker evidence must be allowed a positive age")
        self._evidence = evidence
        self._clock = clock
        self._max_age = max_age

    async def assess(self, request: PromotionRequest) -> Assessment:
        checks = await self._evidence.critical_checks()
        if not checks:
            return Assessment.refused("no broker verification evidence exists")
        now = self._clock.now()
        problems: list[str] = []
        for check in checks:
            if check.outcome is not CheckOutcome.PASS:
                problems.append(f"{check.name}: {check.outcome.value}")
            elif check.checked_at is None or now - check.checked_at > self._max_age:
                problems.append(f"{check.name}: stale (older than {self._max_age.days} days)")
        if problems:
            return Assessment.refused("broker verification not passed: " + "; ".join(problems))
        return Assessment.met(
            EvidenceRef(EvidenceKind.BROKER_VERIFICATION, f"{len(checks)} critical checks")
        )


class LiveAcknowledged:
    """A human typed the acknowledgement phrase for exactly this configuration."""

    name = "live_acknowledged"

    def __init__(self, book: AcknowledgementBook) -> None:
        self._book = book

    async def assess(self, request: PromotionRequest) -> Assessment:
        found = await self._book.get(request.strategy, request.behaviour_hash)
        if found is None:
            phrase = acknowledgement_phrase(request.strategy, request.behaviour_hash)
            return Assessment.refused(
                f"no human acknowledgement for this configuration; run "
                f"`emporos graduation acknowledge {request.strategy}` and type {phrase!r}"
            )
        return Assessment.met(
            EvidenceRef(
                EvidenceKind.ACKNOWLEDGEMENT,
                f"{request.strategy}:{request.behaviour_hash[:8]}",
                f"by {found.operator}",
            )
        )


class NoOpenAnomalies:
    """The kill switch is clear (and readable) and no order is unresolved."""

    name = "no_open_anomalies"

    def __init__(self, switch: SwitchView, orders: UnresolvedOrders) -> None:
        self._switch = switch
        self._orders = orders

    async def assess(self, request: PromotionRequest) -> Assessment:
        problems: list[str] = []
        if await self._switch.halted():
            problems.append("the kill switch is engaged")
        unresolved = await self._orders.count()
        if unresolved:
            problems.append(f"{unresolved} order(s) are unresolved (UNKNOWN or PENDING_NEW)")
        if problems:
            return Assessment.refused("; ".join(problems))
        return Assessment.met()


class JevDependencyAllowed:
    """A Jev-enabled deployment may not be promoted unless the latest `jev_incremental` experiment
    for this configuration is ACCEPTED (EM-187). A deployment that runs without Jev is untouched.

    Today no Jev experiment can reach ACCEPTED, so this refuses every Jev-enabled configuration."""

    name = "jev_dependency_allowed"

    def __init__(self, experiments: ExperimentEvidence) -> None:
        self._experiments = experiments

    async def assess(self, request: PromotionRequest) -> Assessment:
        if not request.jev_enabled:
            return Assessment.met()
        latest = await self._experiments.latest_for(
            request.behaviour_hash, ExperimentFamily.JEV_INCREMENTAL.value
        )
        if latest is None:
            return Assessment.refused(
                "Jev is enabled but no jev_incremental experiment exists for this configuration"
            )
        if latest.outcome is not ExperimentOutcomeLabel.ACCEPTED:
            return Assessment.refused(
                f"Jev is enabled but its latest experiment {latest.experiment_id} is "
                f"{latest.outcome.value}, not accepted"
            )
        return Assessment.met(
            EvidenceRef(EvidenceKind.JEV_EXPERIMENT, latest.experiment_id, "accepted")
        )
