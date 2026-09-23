"""The exchange trading calendar (EM-56).

`StoredTradingCalendar` answers `is_trading_day` from `market_calendar` records; a date with no
record falls back to "weekday = trading day" (correct for the future, where holidays are not yet
known to us). Weekends are never trading days, even if a stray record says so.

Rather than hand-typing exchange holidays, `derive_trading_days` reads them off the broker's own
DAILY bars: a weekday with a daily bar traded, one without did not (recorded live: Mon 2026-09-14
has no bar). `CalendarSeeder` fetches those bars for a liquid reference instrument and stores the
result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Protocol

from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError
from emporos.core.hashing import Canonical, content_hash
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.history.source import HistoricalCandleSource
from emporos.marketdata.session import SessionWindow


class CalendarStore(Protocol):
    async def load_all(self) -> dict[date, bool]: ...

    async def save(self, days: Mapping[date, bool]) -> None: ...


class StoredTradingCalendar:
    """A `TradingCalendar` over an in-memory snapshot; call `refresh` after the store changes."""

    def __init__(self, days: Mapping[date, bool] | None = None) -> None:
        self._days = dict(days or {})

    @classmethod
    async def from_store(cls, store: CalendarStore) -> StoredTradingCalendar:
        return cls(await store.load_all())

    async def refresh(self, store: CalendarStore) -> None:
        self._days = await store.load_all()

    def is_trading_day(self, day: date) -> bool:
        if day.weekday() >= 5:
            return False
        return self._days.get(day, True)

    def known_days(self) -> int:
        return len(self._days)

    def content_hash(self) -> str:
        """A content hash of the loaded snapshot (EM-177): two runs that hashed the same calendar
        checked every date against the same holiday/trading-day knowledge."""
        document: dict[str, Canonical] = {
            "days": [[d.isoformat(), is_trading] for d, is_trading in sorted(self._days.items())]
        }
        return content_hash(document)


def derive_trading_days(daily_bars: Sequence[Candle], first: date, last: date) -> dict[date, bool]:
    """Every weekday in `first..last` -> did the broker report a daily bar for it?"""
    traded = {bar.ts.astimezone(IST).date() for bar in daily_bars}
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return {d: d in traded for d in days if d.weekday() < 5}


class CalendarSeeder:
    def __init__(
        self,
        source: HistoricalCandleSource,
        store: CalendarStore,
        window: SessionWindow | None = None,
    ) -> None:
        self._source = source
        self._store = store
        self._window = window or SessionWindow()

    async def seed(self, reference: Instrument, first: date, last: date) -> dict[date, bool]:
        """Derive and persist trading days for `first..last` from `reference`'s daily bars.

        A reference with NO daily bars in the range is a configuration/data problem, not a
        calendar of all holidays, so it is refused rather than stored."""
        bars = await self._source.fetch(
            reference, Timeframe.D1, self._window.open_at(first), self._window.close_at(last)
        )
        if not bars:
            raise ConfigurationError(
                f"{reference.instrument_id} returned no daily bars for {first}..{last}"
            )
        days = derive_trading_days(bars, first, last)
        await self._store.save(days)
        return days
