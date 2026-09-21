"""A scripted live feed: implements `LiveFeed` / `LiveFeedOpener` exactly as the Angel One one does,
with a hand-driven market and no network, credentials or socket."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta

from emporos.broker.paper.market import MarketDataSource
from emporos.cli.live_feed import FeedRequest, LiveFeed
from emporos.core.clock import FixedClock
from emporos.domain.candles import Candle
from emporos.strategies.config import ResolvedStrategyConfig
from tests.support.paper_market import FakeMarketData


class ScriptedWarmup:
    def __init__(self, bars: Sequence[Candle] = ()) -> None:
        self._bars = list(bars)
        self.requested: list[str] = []

    async def bars_for(self, config: ResolvedStrategyConfig) -> Sequence[Candle]:
        self.requested.append(config.name)
        return self._bars


class ScriptedFeed:
    def __init__(self, source: MarketDataSource, warmup: ScriptedWarmup, events: list[str]) -> None:
        self._source = source
        self._warmup = warmup
        self._events = events

    @property
    def source(self) -> MarketDataSource:
        return self._source

    @property
    def warmup(self) -> ScriptedWarmup:
        return self._warmup

    def running(self) -> AbstractAsyncContextManager[None]:
        return self._running()

    @asynccontextmanager
    async def _running(self) -> AsyncIterator[None]:
        self._events.append("ticks on")
        try:
            yield
        finally:
            self._events.append("ticks off")


class ScriptedFeedOpener:
    """Serves every instrument in the persisted master, so any real strategy file resolves."""

    def __init__(self, warmup: ScriptedWarmup | None = None) -> None:
        self.warmup = warmup or ScriptedWarmup()
        self.events: list[str] = []
        self.market: FakeMarketData | None = None
        self.requests: list[FeedRequest] = []

    @asynccontextmanager
    async def open(self, request: FeedRequest) -> AsyncIterator[LiveFeed]:
        self.requests.append(request)
        self.market = FakeMarketData(await request.master.load_current(), [])
        self.events.append("opened")
        try:
            yield ScriptedFeed(self.market, self.warmup, self.events)
        finally:
            self.events.append("closed")


class AdvancingSleeper:
    """Virtual time: sleeping moves the clock, so a whole session runs in moments."""

    def __init__(self, clock: FixedClock) -> None:
        self._clock = clock

    async def sleep(self, seconds: float) -> None:
        self._clock.advance(timedelta(seconds=seconds))
