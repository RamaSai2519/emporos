"""The live market-data feed a paper worker runs on, and the Angel One implementation of it.

The worker (`live_paper_worker`) depends only on the two small Protocols here, so its orchestration
is testable with a scripted feed. `AngelOneFeedOpener` is the one place the Angel One transport,
the tick socket and the candle pipeline are assembled.

The feed hands the worker a `MarketDataOnly` source: the Angel One broker's order methods are held
privately inside it and are not reachable from anything the paper worker builds. The process logs in
to Angel One for quotes, history and the tick socket, and to nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from pymongo.asynchronous.database import AsyncDatabase

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.factory import AngelOneStackFactory
from emporos.broker.angelone.feed_auth import SessionFeedAuthProvider
from emporos.broker.angelone.ws_orders import OrderUpdateHub
from emporos.broker.backoff import JitterSource
from emporos.broker.paper.market import MarketDataOnly, MarketDataSource
from emporos.cli.broker_composition import BrokerComposer
from emporos.cli.cold_storage import cold_archive
from emporos.cli.market_data_composition import MarketDataComposer
from emporos.cli.worker_composition import RepositoryWarmup, WarmupSource
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import Clock, Sleeper
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.domain.instruments import Instrument
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.store import InstrumentMasterStore
from emporos.marketdata.session import SessionWindow
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleRepository
from emporos.persistence.placement import RetentionPlacement
from emporos.session.bar_feed import ClosedBarQueue
from emporos.session.tripwire import FeedWatch


@dataclass(frozen=True)
class FeedRequest:
    """What a feed needs from the worker that is about to use it."""

    database: AsyncDatabase[Any]
    instruments: InstrumentCache
    master: InstrumentMasterStore
    bars: ClosedBarQueue  # closed candles are pushed here, ready for the strategies
    window: SessionWindow


class LiveFeed(Protocol):
    """Market data for one session: a source the paper broker reads, warm-up history, a lifetime."""

    @property
    def source(self) -> MarketDataSource: ...

    @property
    def warmup(self) -> WarmupSource: ...

    @property
    def watch(self) -> FeedWatch:
        """The feed's own health, for the anomaly tripwire."""
        ...

    def running(self) -> AbstractAsyncContextManager[None]:
        """Ticks flow (and candles close) for as long as this context is open."""
        ...


class LiveFeedOpener(Protocol):
    def open(self, request: FeedRequest) -> AbstractAsyncContextManager[LiveFeed]: ...


class _StoredCatalog:
    """`get_instruments` from the persisted master (the paper broker asks once, at open)."""

    def __init__(self, store: InstrumentMasterStore) -> None:
        self._store = store

    async def load(self) -> list[Instrument]:
        return await self._store.load_current()


class AngelOneFeed:
    def __init__(
        self,
        source: MarketDataSource,
        warmup: WarmupSource,
        running: Callable[[], AbstractAsyncContextManager[None]],
        watch: FeedWatch,
    ) -> None:
        self._source = source
        self._warmup = warmup
        self._running = running
        self._watch = watch

    @property
    def watch(self) -> FeedWatch:
        return self._watch

    @property
    def source(self) -> MarketDataSource:
        return self._source

    @property
    def warmup(self) -> WarmupSource:
        return self._warmup

    def running(self) -> AbstractAsyncContextManager[None]:
        return self._running()


class AngelOneFeedOpener:
    def __init__(
        self, settings: Settings, clock: Clock, sleeper: Sleeper, jitter: JitterSource
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter

    @asynccontextmanager
    async def open(self, request: FeedRequest) -> AsyncIterator[LiveFeed]:
        settings = self._settings
        if not (settings.angelone_api_key and settings.angelone_client_code):
            raise ConfigurationError("ANGELONE_API_KEY and ANGELONE_CLIENT_CODE are required")
        stack = AngelOneStackFactory(settings, self._clock, self._sleeper, self._jitter).build()
        try:
            api = AngelOneApi(stack.transport)
            backfill = AngelOneCandleBackfill(api)
            repository = CandleRepository(
                MongoCandleStore(request.database),
                cold_archive(settings),
                RetentionPlacement(self._clock),
            )
            market = MarketDataComposer(
                auth=SessionFeedAuthProvider(
                    stack.sessions, settings.angelone_api_key, settings.angelone_client_code
                ),
                resolver=request.instruments,
                writer=repository,
                backfill=backfill,
                clock=self._clock,
                sleeper=self._sleeper,
                jitter=self._jitter,
                alerts=LogAlertSink(),
                window=request.window,
            ).build()
            market.aggregator.subscribe(request.bars)
            market.deriver.subscribe(request.bars)
            broker = BrokerComposer(
                api=api,
                sessions=stack.sessions,
                client_code=settings.angelone_client_code,
                resolver=request.instruments,
                catalog=_StoredCatalog(request.master),
                candles=backfill,
                market_data=market.subscriptions,
                ticks=market.ticks,
                order_updates=OrderUpdateHub(),
            ).build()

            @asynccontextmanager
            async def running() -> AsyncIterator[None]:
                market.service.start()
                feed_task = asyncio.create_task(market.client.run())
                try:
                    yield
                finally:
                    await market.service.stop()
                    feed_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await feed_task

            yield AngelOneFeed(
                MarketDataOnly(broker),
                RepositoryWarmup(repository, self._clock),
                running,
                market.watchdog,
            )
        finally:
            try:
                await stack.sessions.logout()
            finally:
                await stack.aclose()
