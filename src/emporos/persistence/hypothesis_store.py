"""The hypothesis registry in Mongo (EM-178): declare and read, nothing else. `_id` is the
hypothesis id, so declaring the same one twice is refused, never overwritten — a hypothesis's
holdout dates can never be moved after they were committed to.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.hypotheses import DuplicateHypothesisError, HypothesisDeclaration
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import HypothesisRecord
from emporos.persistence.repositories import HypothesisRepository


class MongoHypothesisRegistry:
    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        self._records = HypothesisRepository(database)

    async def declare(self, declaration: HypothesisDeclaration) -> None:
        try:
            await self._records.insert(self._record(declaration))
        except DuplicateRecordError as error:
            raise DuplicateHypothesisError(declaration.hypothesis_id) from error

    async def get(self, hypothesis_id: str) -> HypothesisDeclaration | None:
        record = await self._records.get(hypothesis_id)
        return None if record is None else self._declaration(record)

    @staticmethod
    def _record(declaration: HypothesisDeclaration) -> HypothesisRecord:
        return HypothesisRecord(
            _id=declaration.hypothesis_id,
            feature_name=declaration.feature_name,
            feature_version=declaration.feature_version,
            study_first=declaration.study_first.isoformat(),
            study_last=declaration.study_last.isoformat(),
            holdout_first=declaration.holdout_first.isoformat(),
            holdout_last=declaration.holdout_last.isoformat(),
            declared_at=declaration.declared_at,
        )

    @staticmethod
    def _declaration(record: HypothesisRecord) -> HypothesisDeclaration:
        return HypothesisDeclaration(
            hypothesis_id=record.id,
            feature_name=record.feature_name,
            feature_version=record.feature_version,
            study_first=date.fromisoformat(record.study_first),
            study_last=date.fromisoformat(record.study_last),
            holdout_first=date.fromisoformat(record.holdout_first),
            holdout_last=date.fromisoformat(record.holdout_last),
            declared_at=record.declared_at,
        )
