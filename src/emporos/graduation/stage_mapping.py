"""The one place `GraduationStage` (the ledger's vocabulary) meets `DeploymentStatus` (the older
registry metadata in `strategies.metadata`), so there is a single source of truth: the ledger."""

from __future__ import annotations

from emporos.domain.graduation import GraduationStage
from emporos.strategies.metadata import DeploymentStatus

_TO_DEPLOYMENT = {
    GraduationStage.RESEARCH: DeploymentStatus.CANDIDATE,
    GraduationStage.PAPER: DeploymentStatus.PAPER,
    GraduationStage.LIVE_CONSERVATIVE: DeploymentStatus.LIVE_CONSERVATIVE,
    GraduationStage.PRODUCTION: DeploymentStatus.PRODUCTION,
    GraduationStage.RETIRED: DeploymentStatus.RETIRED,
}
_FROM_DEPLOYMENT = {status: stage for stage, status in _TO_DEPLOYMENT.items()}


class StageMapping:
    @staticmethod
    def to_deployment(stage: GraduationStage) -> DeploymentStatus:
        return _TO_DEPLOYMENT[stage]

    @staticmethod
    def from_deployment(status: DeploymentStatus) -> GraduationStage:
        return _FROM_DEPLOYMENT[status]
