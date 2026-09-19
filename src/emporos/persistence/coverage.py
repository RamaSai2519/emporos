"""Mongo store for `DayCoverage` (`history_coverage`, unique per instrument/timeframe/day).

Writes are idempotent upserts, so re-recording a day is harmless. Unlike candles this collection is
not behind `CandleRepository`: it holds bookkeeping about history, not price data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC as _UTC
from datetime import date
from typing import Any

from pymongo import ReplaceOne
from pymongo.asynchronous.database import AsyncDatabase

from emporos.domain.candles import Timeframe
from emporos.domain.coverage import DayCoverage, TimeRange
from emporos.persistence.collections import Collection


class CoverageDocumentMapper:
    @staticmethod
    def key(coverage: DayCoverage) -> dict[str, Any]:
        return {
            "instrument_id": coverage.instrument_id,
            "timeframe": coverage.timeframe,
            "day": coverage.day.isoformat(),
        }

    def to_document(self, coverage: DayCoverage) -> dict[str, Any]:
        return self.key(coverage) | {
            "absent": [{"start": r.start, "end": r.end} for r in coverage.absent],
            "fetched_at": coverage.fetched_at,
            "complete": coverage.complete,
            "empty": coverage.empty,
        }

    @staticmethod
    def from_document(document: Mapping[str, Any]) -> DayCoverage:
        # pymongo returns naive UTC datetimes unless tz_aware is set; normalize defensively
        def aware(moment: Any) -> Any:
            return moment if moment.tzinfo else moment.replace(tzinfo=_UTC)

        return DayCoverage(
            instrument_id=document["instrument_id"],
            timeframe=Timeframe(document["timeframe"]),
            day=date.fromisoformat(document["day"]),
            absent=tuple(
                TimeRange(aware(r["start"]), aware(r["end"])) for r in document.get("absent", [])
            ),
            fetched_at=aware(document["fetched_at"]),
            complete=document.get("complete", True),
            empty=document.get("empty", False),
        )


class MongoCoverageStore:
    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        mapper: CoverageDocumentMapper | None = None,
    ) -> None:
        self._collection = database[Collection.HISTORY_COVERAGE]
        self._mapper = mapper or CoverageDocumentMapper()

    async def get_days(
        self, instrument_id: str, timeframe: Timeframe, first: date, last: date
    ) -> dict[date, DayCoverage]:
        """Coverage for the IST days `first..last` inclusive."""
        query = {
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "day": {"$gte": first.isoformat(), "$lte": last.isoformat()},
        }
        found = [self._mapper.from_document(d) async for d in self._collection.find(query)]
        return {c.day: c for c in found}

    async def save(self, coverages: Sequence[DayCoverage]) -> None:
        if not coverages:
            return
        operations: list[ReplaceOne[Mapping[str, Any]]] = [
            ReplaceOne(self._mapper.key(c), self._mapper.to_document(c), upsert=True)
            for c in coverages
        ]
        await self._collection.bulk_write(operations, ordered=False)
