"""Mongo-backed storage for the trading calendar (`market_calendar`, one record per IST date)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from emporos.persistence.records import MarketCalendarRecord
from emporos.persistence.repositories import MarketCalendarRepository


class MongoCalendarStore:
    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        self._repository = MarketCalendarRepository(database)

    async def load_all(self) -> dict[date, bool]:
        records = await self._repository.find({})
        return {date.fromisoformat(r.date): r.is_trading_day for r in records}

    async def save(self, days: Mapping[date, bool]) -> None:
        for day, is_trading in sorted(days.items()):
            iso = day.isoformat()
            await self._repository.replace(
                MarketCalendarRecord(_id=iso, date=iso, is_trading_day=is_trading), upsert=True
            )
