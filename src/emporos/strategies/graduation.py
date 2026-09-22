"""The reproducible promotion pipeline (EM-152 / EM-166):

    research -> backtest -> walk-forward -> out-of-sample -> paper -> live_conservative
        -> production

Each stage of `ValidationStatus` and `DeploymentStatus` (EM-155's registry metadata) can only be
reached by advancing exactly one stage at a time, backed by a `Verdict` from the evidence-based
gates in `emporos.backtest.robustness.verdict` — never by jumping ahead on the strength of a
single backtest. This module knows nothing about backtests or evidence itself (`strategies` stays
pure); it only enforces the ORDER, given whatever verdict the caller already computed.

A `REJECTED` verdict resets validation to `RESEARCH` — the evidence said start over, not "stay
where you are". An `INCONCLUSIVE` verdict holds at the current stage: thin evidence can withhold
promotion but never demotes a strategy that already validated an earlier stage. Deployment can
never advance past `CANDIDATE` until validation has reached `OUT_OF_SAMPLE_VALIDATED` — the guard
against a strategy reaching real capital on one good-looking backtest.
"""

from __future__ import annotations

from dataclasses import dataclass

from emporos.domain.experiments import Verdict
from emporos.strategies.metadata import DeploymentStatus, ValidationStatus

_VALIDATION_ORDER = (
    ValidationStatus.RESEARCH,
    ValidationStatus.BACKTESTED,
    ValidationStatus.WALK_FORWARD_VALIDATED,
    ValidationStatus.OUT_OF_SAMPLE_VALIDATED,
)
_DEPLOYMENT_ORDER = (
    DeploymentStatus.CANDIDATE,
    DeploymentStatus.PAPER,
    DeploymentStatus.LIVE_CONSERVATIVE,
    DeploymentStatus.PRODUCTION,
)


class GraduationStageError(ValueError):
    """An attempted promotion that is not the very next stage — graduation moves one stage at a
    time, and this is a caller bug, not an ordinary outcome (unlike an inconclusive verdict)."""


@dataclass(frozen=True)
class GraduationOutcome:
    status: ValidationStatus | DeploymentStatus
    promoted: bool
    reason: str


class GraduationGate:
    def advance_validation(
        self, current: ValidationStatus, target: ValidationStatus, verdict: Verdict
    ) -> GraduationOutcome:
        self._require_next(current, target, _VALIDATION_ORDER, "validation")
        if verdict is Verdict.VALIDATED:
            return GraduationOutcome(target, True, f"{target.value}: verdict validated")
        if verdict is Verdict.REJECTED:
            return GraduationOutcome(
                ValidationStatus.RESEARCH, False, "verdict rejected: back to research"
            )
        return GraduationOutcome(
            current, False, "verdict inconclusive: staying at the current stage"
        )

    def advance_deployment(
        self,
        validation_status: ValidationStatus,
        current: DeploymentStatus,
        target: DeploymentStatus,
    ) -> GraduationOutcome:
        if current is DeploymentStatus.RETIRED:
            raise GraduationStageError("a retired strategy cannot be promoted")
        self._require_next(current, target, _DEPLOYMENT_ORDER, "deployment")
        if validation_status is not ValidationStatus.OUT_OF_SAMPLE_VALIDATED:
            return GraduationOutcome(
                current,
                False,
                f"deployment requires out_of_sample_validated evidence, strategy is at "
                f"{validation_status.value}",
            )
        return GraduationOutcome(target, True, f"promoted to {target.value}")

    @staticmethod
    def _require_next(
        current: ValidationStatus | DeploymentStatus,
        target: ValidationStatus | DeploymentStatus,
        order: tuple[ValidationStatus, ...] | tuple[DeploymentStatus, ...],
        kind: str,
    ) -> None:
        if current not in order or target not in order:
            raise GraduationStageError(f"{target} is not a {kind} stage")
        current_index = order.index(current)
        target_index = order.index(target)
        if target_index != current_index + 1:
            raise GraduationStageError(
                f"cannot go from {current.value} to {target.value}: "
                f"{kind} graduation moves one stage at a time"
            )
