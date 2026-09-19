"""Builds an `AngelOneBroker` over doubles, for adapter and contract tests."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from emporos.broker.angelone.adapter import AngelOneBroker
from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.session import Session
from emporos.broker.models import BrokerOrderUpdate
from emporos.core.clock import IST
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.domain.ticks import Tick
from emporos.instruments.cache import InstrumentCache
from tests.support.fakes import ScriptedRestTransport, make_instrument

NOW = datetime(2026, 9, 21, 4, 30, tzinfo=UTC)
SBIN = make_instrument("3045", symbol="SBIN-EQ")
RELIANCE = make_instrument("2885", symbol="RELIANCE-EQ")


def session(tag: str = "1") -> Session:
    return Session(
        f"jwt-{tag}", f"refresh-{tag}", f"feed-{tag}", NOW, datetime(2026, 9, 22, tzinfo=IST)
    )


class StubSessions:
    def __init__(self) -> None:
        self.logins = 0
        self.logouts = 0
        self._current: Session | None = None

    async def authenticate(self) -> Session:
        self.logins += 1
        self._current = session(str(self.logins))
        return self._current

    async def session(self) -> Session:
        return self._current or await self.authenticate()

    async def logout(self) -> None:
        self.logouts += 1
        self._current = None


class StubCatalog:
    def __init__(self, instruments: Sequence[Instrument]) -> None:
        self.instruments = instruments

    async def load(self) -> Sequence[Instrument]:
        return self.instruments


class RecordingCandleFetcher:
    def __init__(
        self, bars: Callable[[Instrument, datetime, datetime], list[Candle]] | None = None
    ):
        self.calls: list[tuple[str, Timeframe, datetime, datetime]] = []
        self._bars = bars or (lambda *_: [])

    async def fetch(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.calls.append((instrument.instrument_id, timeframe, start, end))
        return self._bars(instrument, start, end)


class RecordingMarketData:
    def __init__(self) -> None:
        self.subscribed: list[list[str]] = []
        self.unsubscribed: list[list[str]] = []

    async def subscribe(self, instruments: Iterable[Instrument]) -> None:
        self.subscribed.append([i.instrument_id for i in instruments])

    async def unsubscribe(self, instruments: Iterable[Instrument]) -> None:
        self.unsubscribed.append([i.instrument_id for i in instruments])


class HandlerList:
    """`TickRegistry` / `OrderUpdateRegistry` double that lets a test push events."""

    def __init__(self) -> None:
        self.handlers: list[Callable[[Any], None]] = []

    def add_handler(self, handler: Callable[[Any], None]) -> None:
        self.handlers.append(handler)

    def emit(self, event: Tick | BrokerOrderUpdate) -> None:
        for handler in self.handlers:
            handler(event)


class BrokerRig:
    def __init__(self, transport: ScriptedRestTransport | None = None) -> None:
        self.transport = transport or ScriptedRestTransport()
        self.sessions = StubSessions()
        self.candles = RecordingCandleFetcher()
        self.market_data = RecordingMarketData()
        self.ticks = HandlerList()
        self.updates = HandlerList()
        self.catalog = StubCatalog([SBIN, RELIANCE])
        self.broker = AngelOneBroker(
            AngelOneApi(self.transport),
            self.sessions,
            "A0000000",
            InstrumentCache([SBIN, RELIANCE]),
            self.catalog,
            self.candles,
            self.market_data,
            self.ticks,
            self.updates,
        )
