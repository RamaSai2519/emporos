"""The coverage ledger kept beside the archived bars, not in Mongo.

Deep history is years of trading days for every instrument; a Mongo document per instrument-day is
tens of thousands of documents (and an index over them) that nothing but a resumable backfill ever
reads. Here one small JSON object per instrument-timeframe-month holds that month's days:

    coverage/{timeframe}/{instrument_id}/{YYYY-MM}.json

It implements the same `CoverageStore` the history layer already depends on, so a backfill runs on
either ledger unchanged. A save merges into what is there (a day rewritten replaces its own entry),
so re-running a chunk is harmless.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from emporos.domain.candles import Timeframe
from emporos.domain.coverage import DayCoverage, TimeRange
from emporos.persistence.object_store import ObjectNotFoundError, ObjectStore


class ObjectCoverageStore:
    def __init__(self, store: ObjectStore) -> None:
        self._store = store

    async def get_days(
        self, instrument_id: str, timeframe: Timeframe, first: date, last: date
    ) -> dict[date, DayCoverage]:
        """Coverage for the IST days `first..last` inclusive."""
        found: dict[date, DayCoverage] = {}
        for month in self._months(first, last):
            for coverage in (await self._read(instrument_id, timeframe, month)).values():
                if first <= coverage.day <= last:
                    found[coverage.day] = coverage
        return found

    async def save(self, coverages: Sequence[DayCoverage]) -> None:
        grouped: dict[tuple[str, Timeframe, str], list[DayCoverage]] = defaultdict(list)
        for coverage in coverages:
            grouped[(coverage.instrument_id, coverage.timeframe, coverage.day.strftime("%Y-%m"))]\
                .append(coverage)  # fmt: skip
        for (instrument_id, timeframe, month), incoming in grouped.items():
            merged = await self._read(instrument_id, timeframe, month)
            merged.update({c.day: c for c in incoming})
            document = [self._encode(merged[day]) for day in sorted(merged)]
            await self._store.put(
                self._key(instrument_id, timeframe, month),
                json.dumps(document, indent=None, separators=(",", ":")).encode(),
            )

    async def _read(
        self, instrument_id: str, timeframe: Timeframe, month: str
    ) -> dict[date, DayCoverage]:
        try:
            data = await self._store.get(self._key(instrument_id, timeframe, month))
        except ObjectNotFoundError:
            return {}
        decoded = (self._decode(instrument_id, timeframe, row) for row in json.loads(data))
        return {c.day: c for c in decoded}

    @staticmethod
    def _key(instrument_id: str, timeframe: Timeframe, month: str) -> str:
        return f"coverage/{timeframe.value}/{instrument_id.replace(':', '_')}/{month}.json"

    @staticmethod
    def _months(first: date, last: date) -> list[str]:
        months: list[str] = []
        cursor = first.replace(day=1)
        while cursor <= last:
            months.append(cursor.strftime("%Y-%m"))
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        return months

    @staticmethod
    def _encode(coverage: DayCoverage) -> dict[str, Any]:
        return {
            "day": coverage.day.isoformat(),
            "fetched_at": coverage.fetched_at.isoformat(),
            "complete": coverage.complete,
            "empty": coverage.empty,
            "absent": [[r.start.isoformat(), r.end.isoformat()] for r in coverage.absent],
        }

    @staticmethod
    def _decode(instrument_id: str, timeframe: Timeframe, row: dict[str, Any]) -> DayCoverage:
        def moment(text: str) -> datetime:
            return datetime.fromisoformat(text).astimezone(UTC)

        return DayCoverage(
            instrument_id=instrument_id,
            timeframe=timeframe,
            day=date.fromisoformat(row["day"]),
            absent=tuple(TimeRange(moment(a), moment(b)) for a, b in row["absent"]),
            fetched_at=moment(row["fetched_at"]),
            complete=row["complete"],
            empty=row["empty"],
        )
