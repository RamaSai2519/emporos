"""Composition root for the market-data stack (Decision: concrete adapters are wired only here).

Assembles, in the order that matters:

    socket -> TickFrameReader -> BoundedTickQueue -> TickPipeline
                                                        |-> TickNormalizer -> [aggregator, watchdog]
    aggregator -> [persister, deriver -> persister]     (closed candles, upserted via CandleWriter)
    feed listeners, in order: SubscriptionManager, aggregator (gap tracking), watchdog, recovery

The subscription manager is registered first so the watchlist is restored before recovery starts
its backfill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.feed_auth import FeedAuthProvider
from emporos.broker.angelone.ws_frames import TickFrameParser, TickFrameReader
from emporos.broker.angelone.ws_market import (
    RECONNECT_POLICY,
    WS_URL,
    FeedMode,
    MarketFeedClient,
)
from emporos.broker.angelone.ws_subscriptions import FeedSubscriptionAdapter
from emporos.broker.backoff import BackoffPolicy, JitterSource
from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock, Sleeper
from emporos.domain.instruments import InstrumentResolver
from emporos.marketdata.aggregator import DEFAULT_GRACE, MinuteCandleAggregator
from emporos.marketdata.broadcast import TickBroadcaster
from emporos.marketdata.candle_writer import CandlePersister
from emporos.marketdata.normalizer import TickNormalizer
from emporos.marketdata.pipeline import TickPipeline
from emporos.marketdata.queue import BoundedTickQueue
from emporos.marketdata.recovery import CandleBackfillSource, ReconnectRecovery
from emporos.marketdata.scheduler import MinuteScheduler
from emporos.marketdata.service import MarketDataService
from emporos.marketdata.session import SessionWindow
from emporos.marketdata.staleness import StalenessWatchdog, ThresholdPolicy
from emporos.marketdata.subscriptions import SubscriptionLimits, SubscriptionManager
from emporos.marketdata.timeframes import TimeframeDeriver
from emporos.persistence.candles import CandleWriter

__all__ = ["AngelOneCandleBackfill", "MarketDataComposer", "MarketDataStack"]


@dataclass(frozen=True)
class MarketDataStack:
    client: MarketFeedClient
    reader: TickFrameReader
    queue: BoundedTickQueue
    pipeline: TickPipeline
    aggregator: MinuteCandleAggregator
    deriver: TimeframeDeriver
    persister: CandlePersister
    watchdog: StalenessWatchdog
    subscriptions: SubscriptionManager
    recovery: ReconnectRecovery
    service: MarketDataService
    ticks: TickBroadcaster


@dataclass(frozen=True, kw_only=True)
class MarketDataComposer:
    auth: FeedAuthProvider
    resolver: InstrumentResolver
    writer: CandleWriter
    backfill: CandleBackfillSource
    clock: Clock
    sleeper: Sleeper
    jitter: JitterSource
    alerts: AlertSink | None = None
    url: str = WS_URL
    feed_mode: FeedMode = FeedMode.QUOTE
    window: SessionWindow | None = None
    grace: timedelta = DEFAULT_GRACE
    limits: SubscriptionLimits | None = None
    staleness: ThresholdPolicy | None = None
    reconnect_policy: BackoffPolicy = RECONNECT_POLICY

    def build(self) -> MarketDataStack:
        queue = BoundedTickQueue(self.clock, self.alerts)
        reader = TickFrameReader(TickFrameParser(), queue, self.alerts)
        client = MarketFeedClient(
            self.auth,
            reader,
            _NullListener(),
            self.clock,
            self.sleeper,
            self.jitter,
            url=self.url,
            reconnect_policy=self.reconnect_policy,
        )
        subscriptions = SubscriptionManager(
            FeedSubscriptionAdapter(client, self.feed_mode), self.limits
        )
        aggregator = MinuteCandleAggregator(self.clock, self.window, self.grace)
        deriver = TimeframeDeriver(window=self.window)
        persister = CandlePersister(self.writer, self.alerts)
        watchdog = StalenessWatchdog(self.clock, self.staleness, self.window, self.alerts)
        recovery = ReconnectRecovery(
            subscriptions,
            self.backfill,
            self.writer,
            self.clock,
            self.sleeper,
            self.grace,
            persister,
            self.window,
            self.alerts,
        )
        aggregator.subscribe(persister)
        aggregator.subscribe(deriver)
        deriver.subscribe(persister)
        for listener in (subscriptions, aggregator, watchdog, recovery):
            client.add_listener(listener)
        normalizer = TickNormalizer(self.resolver, self.window)
        ticks = TickBroadcaster()  # `Broker.on_tick` registers here
        pipeline = TickPipeline(queue, normalizer, (aggregator, watchdog, ticks), self.alerts)
        scheduler = MinuteScheduler(aggregator, persister, self.clock, self.sleeper, self.grace)
        service = MarketDataService(pipeline, scheduler, watchdog, self.sleeper)
        return MarketDataStack(
            client=client,
            reader=reader,
            queue=queue,
            pipeline=pipeline,
            aggregator=aggregator,
            deriver=deriver,
            persister=persister,
            watchdog=watchdog,
            subscriptions=subscriptions,
            recovery=recovery,
            service=service,
            ticks=ticks,
        )


class _NullListener:
    """The client requires a first listener at construction; the real ones are added after."""

    async def on_connected(self) -> None:
        return None

    async def on_disconnected(self, reason: str) -> None:
        del reason
