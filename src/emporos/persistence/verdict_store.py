"""Recorded verdicts in Mongo: append and read, and nothing else.

Like the trial ledger it holds a `Repository` rather than being one, so a verdict cannot be edited
or deleted through it: a strategy that was judged cannot be quietly re-judged. A new curation
appends a new verdict; the LATEST one for a strategy is the one that applies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.experiments import Verdict
from emporos.domain.verdicts import GateFinding, RecordedVerdict
from emporos.persistence.collections import Collection
from emporos.persistence.records import StrategyVerdictRecord
from emporos.persistence.repository import Repository


class MongoVerdictBook:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        collection: str = Collection.STRATEGY_VERDICTS,
    ) -> None:
        self._records = Repository(database, collection, StrategyVerdictRecord)

    async def append(self, verdict: RecordedVerdict, verdict_id: str) -> None:
        await self._records.insert(self._record(verdict, verdict_id))

    async def latest(self, strategy: str) -> RecordedVerdict | None:
        found = await self._records.find(
            {"strategy": strategy}, sort=[("recorded_at", DESCENDING)], limit=1
        )
        return self._verdict(found[0]) if found else None

    async def latest_of_each(self) -> dict[str, RecordedVerdict]:
        newest: dict[str, RecordedVerdict] = {}
        for record in await self._records.find({}, sort=[("recorded_at", DESCENDING)]):
            newest.setdefault(record.strategy, self._verdict(record))
        return newest

    @staticmethod
    def _record(verdict: RecordedVerdict, verdict_id: str) -> StrategyVerdictRecord:
        return StrategyVerdictRecord(
            _id=verdict_id,
            strategy=verdict.strategy,
            behaviour_hash=verdict.behaviour_hash,
            verdict=verdict.verdict.value,
            gates=[
                {"name": g.name, "outcome": g.outcome, "detail": g.detail} for g in verdict.gates
            ],
            capital=verdict.capital,
            first_day=verdict.first_day,
            last_day=verdict.last_day,
            experiment=verdict.experiment,
            source=verdict.source,
            recorded_at=verdict.recorded_at,
            notes=list(verdict.notes),
        )

    @staticmethod
    def _verdict(record: StrategyVerdictRecord) -> RecordedVerdict:
        return RecordedVerdict(
            strategy=record.strategy,
            behaviour_hash=record.behaviour_hash,
            verdict=Verdict(record.verdict),
            gates=tuple(GateFinding(g["name"], g["outcome"], g["detail"]) for g in record.gates),
            capital=record.capital,
            first_day=record.first_day,
            last_day=record.last_day,
            experiment=record.experiment,
            source=record.source,
            recorded_at=record.recorded_at,
            notes=tuple(record.notes),
        )
