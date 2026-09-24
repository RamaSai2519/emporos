"""What graduation needs from the outside, as narrow protocols (Interface Segregation).

Each requirement depends on exactly the one it reads. The composition root binds them to Mongo,
the experiment registry and the broker-verification evidence; tests bind fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from emporos.domain.graduation import GraduationEvent, LiveAcknowledgement
from emporos.domain.research_experiments import ExperimentOutcomeLabel


class GraduationLedger(Protocol):
    async def latest(self, strategy: str) -> GraduationEvent | None: ...

    async def history(self, strategy: str) -> list[GraduationEvent]: ...

    async def append(self, event: GraduationEvent) -> None: ...


class AcknowledgementBook(Protocol):
    async def get(self, strategy: str, behaviour_hash: str) -> LiveAcknowledgement | None: ...

    async def record(self, acknowledgement: LiveAcknowledgement) -> None: ...


@dataclass(frozen=True)
class ExperimentEvidenceView:
    """The parts of a published experiment report that graduation judges, and nothing more."""

    experiment_id: str
    family: str
    outcome: ExperimentOutcomeLabel
    declared_at: datetime
    behaviour_hashes: frozenset[str]  # every configuration the report was run on
    holdout_reserved: bool  # the report has a holdout period
    unsettled_reason_codes: frozenset[str]  # codes of reasons that are not a pass
    # None means the report did not record it, which is not the same as "none were assumed".
    assumed_instrument_ids: tuple[str, ...] | None
    quarantine_hash: str | None


class ExperimentEvidence(Protocol):
    async def get(self, experiment_id: str) -> ExperimentEvidenceView | None: ...

    async def latest_for(self, behaviour_hash: str, family: str) -> ExperimentEvidenceView | None:
        """The newest published report of this family that was run on this configuration."""
        ...


class CurrentQuarantine(Protocol):
    async def hash(self) -> str:
        """The content hash of the corporate-action quarantine in force now."""
        ...


class UnresolvedOrders(Protocol):
    async def count(self) -> int:
        """Orders currently UNKNOWN or still PENDING_NEW at the broker."""
        ...
