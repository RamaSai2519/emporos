"""The broker verification log in Mongo: append and read, nothing else (EM-186).

It holds a `Repository` rather than being one, so a recorded result cannot be edited or deleted
through it. The latest result per check is what graduation sees; older ones remain as history.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.domain.broker_verification import CheckOutcome, CheckResult
from emporos.persistence.collections import Collection
from emporos.persistence.records import BrokerVerificationCheckRecord
from emporos.persistence.repository import Repository


class MongoBrokerVerificationLog:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        ids: IdGenerator,
        collection: str = Collection.BROKER_VERIFICATION_CHECKS,
    ) -> None:
        self._records = Repository(database, collection, BrokerVerificationCheckRecord)
        self._ids = ids

    async def append(self, result: CheckResult) -> None:
        await self._records.insert(
            BrokerVerificationCheckRecord(
                _id=self._ids.new_ulid(),
                name=result.name,
                outcome=result.outcome.value,
                checked_at=result.checked_at,
                evidence_ref=result.evidence_ref,
                detail=result.detail,
                recorded_by=result.recorded_by,
            )
        )

    async def latest(self, name: str) -> CheckResult | None:
        found = await self._records.find({"name": name}, sort=[("checked_at", DESCENDING)], limit=1)
        return self._result(found[0]) if found else None

    async def history(self, name: str | None = None) -> Sequence[CheckResult]:
        query: dict[str, Any] = {} if name is None else {"name": name}
        found = await self._records.find(query, sort=[("checked_at", ASCENDING)])
        return [self._result(r) for r in found]

    @staticmethod
    def _result(record: BrokerVerificationCheckRecord) -> CheckResult:
        return CheckResult(
            name=record.name,
            outcome=CheckOutcome(record.outcome),
            checked_at=record.checked_at,
            evidence_ref=record.evidence_ref,
            detail=record.detail,
            recorded_by=record.recorded_by,
        )
