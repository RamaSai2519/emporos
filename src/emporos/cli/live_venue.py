"""The live venue: the one place in this process that may hold an order-capable broker.

Reuses the same Angel One stack, market-data pipeline and candle backfill `live_feed.py` already
wires for the paper-on-live-data path (`AngelOneFeedOpener`), but does NOT narrow the broker to
`MarketDataOnly` — it hands out the full `Broker`, ABC-checked, only to `LiveWorkerComposer`. It
also connects the order-update socket (`order_stream_client`, built but unused until now) and
requires it to report connected before `VenueHealth.order_feed_ok()` says so, which is what keeps
`BrokerHealthGuard` closed until a live worker can actually hear what happened to an order it
placed (docs/live-trading.md: "a venue that forgets the order socket would trade nothing, safely").
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.factory import AngelOneStackFactory
from emporos.broker.angelone.feed_auth import SessionFeedAuthProvider
from emporos.broker.angelone.mapping import AccountMapper
from emporos.broker.angelone.ws_orders import (
    OrderUpdateHub,
    OrderUpdateParser,
    OrderUpdateReader,
    order_stream_client,
)
from emporos.broker.backoff import JitterSource
from emporos.broker.base import Broker
from emporos.broker.models import MarketDataMode
from emporos.cli.broker_composition import BrokerComposer
from emporos.cli.cold_storage import cold_archive
from emporos.cli.live_feed import FeedRequest
from emporos.cli.market_data_composition import MarketDataComposer
from emporos.cli.worker_composition import RepositoryWarmup, WarmupSource
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import Clock, Sleeper
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.domain.instruments import Instrument
from emporos.instruments.store import InstrumentMasterStore
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleRepository
from emporos.persistence.placement import RetentionPlacement
from emporos.session.risk_facts import VenueHealth
from emporos.session.worker import SessionVenue


class _StoredCatalog:
    """`get_instruments` from the persisted master (the broker asks once, at open)."""

    def __init__(self, store: InstrumentMasterStore) -> None:
        self._store = store

    async def load(self) -> list[Instrument]:
        return await self._store.load_current()


class SocketClient(Protocol):
    """What `LiveVenue` needs of a `MarketFeedClient`: run until stopped, then stop cleanly."""

    async def run(self) -> None: ...

    async def stop(self) -> None: ...


class _OrderFeedHealth:
    """The order socket's `ConnectionListener`: the only thing that may say the order feed is up."""

    def __init__(self, health: VenueHealth) -> None:
        self._health = health

    async def on_connected(self) -> None:
        self._health.set_feed(True)

    async def on_disconnected(self, reason: str) -> None:
        del reason
        self._health.set_feed(False)


class LiveVenue:
    """Log in, subscribe market data, and require a confirmed order-update socket before the risk
    engine will allow an order. `authenticate`/`connect`/`close` is the whole `SessionVenue`
    contract; nothing else here is reachable from strategies, risk or execution."""

    def __init__(
        self,
        broker: Broker,
        market_client: SocketClient,
        order_client: SocketClient,
        instruments: Sequence[str],
        health: VenueHealth,
    ) -> None:
        self._broker = broker
        self._market_client = market_client
        self._order_client = order_client
        self._instruments = list(instruments)
        self._health = health
        self._tasks: list[asyncio.Task[None]] = []

    async def authenticate(self) -> None:
        await self._broker.ensure_session()
        self._health.set_session(True)

    async def connect(self) -> None:
        await self._broker.subscribe_market_data(self._instruments, MarketDataMode.QUOTE)
        self._tasks = [
            asyncio.create_task(self._market_client.run()),
            asyncio.create_task(self._order_client.run()),
        ]

    async def close(self) -> None:
        self._health.set_session(False)
        self._health.set_feed(False)
        await self._market_client.stop()
        await self._order_client.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


@dataclass(frozen=True)
class LiveConnection:
    """What a live worker needs from the venue opener: the unwrapped broker, the venue that owns
    both sockets, the shared health it reports to, and warm-up history."""

    broker: Broker
    venue: SessionVenue
    health: VenueHealth
    warmup: WarmupSource


class LiveVenueOpener(Protocol):
    def open(
        self, request: FeedRequest, instruments: Sequence[str]
    ) -> AbstractAsyncContextManager[LiveConnection]: ...


class AngelOneLiveVenueOpener:
    """Builds the same Angel One stack and market-data pipeline `AngelOneFeedOpener` does for
    paper, plus the order-update socket, and hands out the broker whole instead of narrowing it.
    The only composer allowed to do this."""

    def __init__(
        self, settings: Settings, clock: Clock, sleeper: Sleeper, jitter: JitterSource
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter

    @asynccontextmanager
    async def open(
        self, request: FeedRequest, instruments: Sequence[str]
    ) -> AsyncIterator[LiveConnection]:
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

            health = VenueHealth()
            order_hub = OrderUpdateHub()
            order_reader = OrderUpdateReader(
                OrderUpdateParser(AccountMapper(), self._clock), order_hub
            )
            order_client = order_stream_client(
                stack.sessions, order_reader, _OrderFeedHealth(health), self._clock,
                self._sleeper, self._jitter,
            )  # fmt: skip

            broker: Broker = BrokerComposer(
                api=api,
                sessions=stack.sessions,
                client_code=settings.angelone_client_code,
                resolver=request.instruments,
                catalog=_StoredCatalog(request.master),
                candles=backfill,
                market_data=market.subscriptions,
                ticks=market.ticks,
                order_updates=order_hub,
            ).build()

            market.service.start()
            venue = LiveVenue(broker, market.client, order_client, instruments, health)
            try:
                yield LiveConnection(
                    broker, venue, health, RepositoryWarmup(repository, self._clock)
                )
            finally:
                await market.service.stop()
        finally:
            try:
                await stack.sessions.logout()
            finally:
                await stack.aclose()
