"""Mongo-backed storage for the corporate-action quarantine (`corporate_action_quarantine`,
EM-177)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from emporos.history.quarantine import QuarantineEntry, QuarantineSource
from emporos.persistence.records import QuarantineRecord
from emporos.persistence.repositories import QuarantineRepository


class MongoQuarantineStore:
    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        self._repository = QuarantineRepository(database)

    async def load_all(self) -> tuple[QuarantineEntry, ...]:
        records = await self._repository.find({})
        return tuple(self._to_entry(r) for r in records)

    async def add(self, entries: Sequence[QuarantineEntry]) -> None:
        for entry in entries:
            record_id = f"{entry.instrument_id}:{entry.day.isoformat()}"
            await self._repository.replace(
                QuarantineRecord(
                    _id=record_id, instrument_id=entry.instrument_id, day=entry.day.isoformat(),
                    reason=entry.reason, source=entry.source.value, recorded_at=entry.recorded_at,
                ),  # fmt: skip
                upsert=True,
            )

    @staticmethod
    def _to_entry(record: QuarantineRecord) -> QuarantineEntry:
        return QuarantineEntry(
            record.instrument_id, date.fromisoformat(record.day), record.reason,
            QuarantineSource(record.source), record.recorded_at,
        )  # fmt: skip
