"""The graduation ledger and the acknowledgement book in Mongo: append and read, nothing else.

Like the verdict book they hold a `Repository` rather than being one, so an event cannot be edited
or deleted through them. `seq` is monotonic per strategy and `(strategy, seq)` is unique, so two
racing promotions cannot both win: the loser gets `GraduationConflictError`, reloads, and decides
again. An acknowledgement is unique per `(strategy, behaviour_hash)`: recording one twice is
refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.errors import DefinitiveError
from emporos.core.ids import IdGenerator
from emporos.domain.graduation import (
    EvidenceKind,
    EvidenceRef,
    GraduationEvent,
    GraduationStage,
    LiveAcknowledgement,
    TransitionKind,
)
from emporos.persistence.collections import Collection
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import GraduationEventRecord, LiveAcknowledgementRecord
from emporos.persistence.repository import Repository


class GraduationConflictError(DefinitiveError):
    """Another transition took this sequence number first: reload the history and decide again."""


class AcknowledgementExistsError(DefinitiveError):
    """This configuration was already acknowledged; an acknowledgement is recorded once."""


class MongoGraduationLedger:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        ids: IdGenerator,
        collection: str = Collection.GRADUATION_EVENTS,
    ) -> None:
        self._records = Repository(database, collection, GraduationEventRecord)
        self._ids = ids

    async def append(self, event: GraduationEvent) -> None:
        try:
            await self._records.insert(self._record(event))
        except DuplicateRecordError as error:
            raise GraduationConflictError(
                f"{event.strategy} already has a graduation event #{event.seq}"
            ) from error

    async def latest(self, strategy: str) -> GraduationEvent | None:
        found = await self._records.find(
            {"strategy": strategy}, sort=[("seq", DESCENDING)], limit=1
        )
        return self._event(found[0]) if found else None

    async def history(self, strategy: str) -> list[GraduationEvent]:
        found = await self._records.find({"strategy": strategy}, sort=[("seq", ASCENDING)])
        return [self._event(r) for r in found]

    def _record(self, event: GraduationEvent) -> GraduationEventRecord:
        return GraduationEventRecord(
            _id=self._ids.new_ulid(),
            strategy=event.strategy,
            behaviour_hash=event.behaviour_hash,
            seq=event.seq,
            from_stage=event.from_stage.value,
            to_stage=event.to_stage.value,
            kind=event.kind.value,
            evidence=[
                {"kind": e.kind.value, "ref": e.ref, "detail": e.detail} for e in event.evidence
            ],
            actor=event.actor,
            reason=event.reason,
            at=event.at,
        )

    @staticmethod
    def _event(record: GraduationEventRecord) -> GraduationEvent:
        return GraduationEvent(
            strategy=record.strategy,
            behaviour_hash=record.behaviour_hash,
            seq=record.seq,
            from_stage=GraduationStage(record.from_stage),
            to_stage=GraduationStage(record.to_stage),
            kind=TransitionKind(record.kind),
            evidence=tuple(
                EvidenceRef(EvidenceKind(e["kind"]), e["ref"], e.get("detail", ""))
                for e in record.evidence
            ),
            actor=record.actor,
            reason=record.reason,
            at=record.at,
        )


class MongoAcknowledgementBook:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        ids: IdGenerator,
        collection: str = Collection.LIVE_ACKNOWLEDGEMENTS,
    ) -> None:
        self._records = Repository(database, collection, LiveAcknowledgementRecord)
        self._ids = ids

    async def record(self, acknowledgement: LiveAcknowledgement) -> None:
        record = LiveAcknowledgementRecord(
            _id=self._ids.new_ulid(),
            strategy=acknowledgement.strategy,
            behaviour_hash=acknowledgement.behaviour_hash,
            operator=acknowledgement.operator,
            typed_phrase=acknowledgement.typed_phrase,
            risk_tier=acknowledgement.risk_tier,
            at=acknowledgement.at,
        )
        try:
            await self._records.insert(record)
        except DuplicateRecordError as error:
            raise AcknowledgementExistsError(
                f"{acknowledgement.strategy} ({acknowledgement.behaviour_hash[:8]}) "
                "is already acknowledged"
            ) from error

    async def get(self, strategy: str, behaviour_hash: str) -> LiveAcknowledgement | None:
        found = await self._records.find_one(
            {"strategy": strategy, "behaviour_hash": behaviour_hash}
        )
        if found is None:
            return None
        return LiveAcknowledgement(
            strategy=found.strategy,
            behaviour_hash=found.behaviour_hash,
            operator=found.operator,
            typed_phrase=found.typed_phrase,
            risk_tier=found.risk_tier,
            at=found.at,
        )
