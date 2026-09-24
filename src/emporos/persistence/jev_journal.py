"""The Jev decision journal in Mongo (EM-187): append and read, nothing else.

Holds a `Repository` rather than being one, so a recorded answer cannot be edited or deleted
through it. The unique index on (request_hash, prompt_hash, model) makes a repeated question a
no-op: the first recorded answer stands, which is what lets every later run replay it. This is
also where "Jev decisions are persisted for auditability" (EM-161) lands.

Structurally satisfies `emporos.jev.replay.JevDecisionJournal` without importing it: persistence
may not import `emporos.jev`, only the pure `JevDecisionRecord` in `domain`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.jev_records import JevDecisionRecord
from emporos.persistence.collections import Collection
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import JevDecisionDocument
from emporos.persistence.repository import Repository


class MongoJevDecisionJournal:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        collection: str = Collection.JEV_DECISIONS,
    ) -> None:
        self._records = Repository(database, collection, JevDecisionDocument)

    async def append(self, record: JevDecisionRecord) -> bool:
        try:
            await self._records.insert(self._document(record))
        except DuplicateRecordError:
            return False
        return True

    async def get(
        self, request_hash: str, prompt_hash: str, model: str
    ) -> JevDecisionRecord | None:
        found = await self._records.find(
            {"request_hash": request_hash, "prompt_hash": prompt_hash, "model": model}, limit=1
        )
        return self._record(found[0]) if found else None

    async def records(self, model: str, prompt_hash: str) -> list[JevDecisionRecord]:
        """Every answer of one model to one prompt, oldest question first: what a research study
        reads, never the live model."""
        found = await self._records.find(
            {"model": model, "prompt_hash": prompt_hash}, sort=[("as_of", 1)]
        )
        return [self._record(d) for d in found]

    @staticmethod
    def _document(record: JevDecisionRecord) -> JevDecisionDocument:
        return JevDecisionDocument(
            _id="|".join(record.key),
            request_hash=record.request_hash,
            as_of=record.as_of,
            symbol=record.symbol,
            strategy=record.strategy,
            mode=record.mode,
            decision=record.decision,
            confidence=str(record.confidence) if record.confidence is not None else None,
            provider=record.provider,
            model=record.model,
            prompt_version=record.prompt_version,
            prompt_hash=record.prompt_hash,
            tokens_used=record.tokens_used,
            latency_ms=record.latency_ms,
            recorded_at=record.recorded_at,
        )

    @staticmethod
    def _record(document: JevDecisionDocument) -> JevDecisionRecord:
        return JevDecisionRecord(
            request_hash=document.request_hash,
            as_of=_aware(document.as_of),
            symbol=document.symbol,
            strategy=document.strategy,
            mode=document.mode,
            decision=document.decision,
            confidence=Decimal(document.confidence) if document.confidence is not None else None,
            provider=document.provider,
            model=document.model,
            prompt_version=document.prompt_version,
            prompt_hash=document.prompt_hash,
            tokens_used=document.tokens_used,
            latency_ms=document.latency_ms,
            recorded_at=_aware(document.recorded_at),
        )


def _aware(moment: datetime) -> datetime:
    """Mongo hands datetimes back naive (UTC); the domain record insists on aware ones."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
