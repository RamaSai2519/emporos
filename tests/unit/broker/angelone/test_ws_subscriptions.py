"""EM-50: the feed-subscription adapter, and the whole chain against an in-process feed server —
including the acceptance test that a reconnect restores the full active watchlist."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest

from emporos.broker.angelone.ws_market import FeedMode, MarketFeedClient
from emporos.broker.angelone.ws_subscriptions import FeedSubscriptionAdapter
from emporos.broker.backoff import BackoffPolicy
from emporos.core.clock import SystemClock
from emporos.domain.instruments import Exchange
from emporos.marketdata.subscriptions import (
    SubscriptionLimitExceededError,
    SubscriptionLimits,
    SubscriptionManager,
)
from tests.support.fakes import make_instrument
from tests.support.ws_doubles import (
    FixedJitter,
    RealSleeper,
    RecordingAuth,
    RecordingHandler,
    RecordingListener,
)
from tests.support.ws_server import FakeFeedServer


class Chain:
    """manager -> adapter -> client -> real socket -> fake server, wired without a bind step."""

    def __init__(self, url: str, limit: int = 200, mode: FeedMode = FeedMode.QUOTE) -> None:
        self.observer = RecordingListener()
        self.client = MarketFeedClient(
            RecordingAuth(),
            RecordingHandler(),
            self.observer,
            SystemClock(),
            RealSleeper(),
            FixedJitter(),
            url=url,
            reconnect_policy=BackoffPolicy(base_delay=0.02, max_delay=0.05, max_attempts=1),
        )
        self.adapter = FeedSubscriptionAdapter(self.client, mode)
        self.manager = SubscriptionManager(self.adapter, SubscriptionLimits(limit))
        self.client.add_listener(self.manager)
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.client.run())

    async def shutdown(self) -> None:
        await self.client.stop()
        if self.task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.task, 3)


@pytest.fixture
async def server() -> AsyncIterator[FakeFeedServer]:
    async with FakeFeedServer().running() as running:
        yield running


def tokens_of(message: dict[str, object]) -> dict[int, list[str]]:
    params = message["params"]
    assert isinstance(params, dict)
    return {t["exchangeType"]: t["tokens"] for t in params["tokenList"]}


async def test_the_adapter_groups_by_exchange_and_uses_quote_mode(server: FakeFeedServer) -> None:
    chain = Chain(server.url)
    chain.start()
    try:
        await server.wait_for(lambda: chain.observer.connected == 1)
        await chain.manager.subscribe(
            [
                make_instrument("3045"),
                make_instrument("500325", Exchange.BSE),
                make_instrument("2885"),
            ]
        )
        await server.wait_for(lambda: len(server.control_messages) == 2)
    finally:
        await chain.shutdown()

    by_exchange = {}
    for message in server.control_messages:
        assert message["action"] == 1 and message["params"]["mode"] == 2  # subscribe, QUOTE
        by_exchange.update(tokens_of(message))
    assert by_exchange == {1: ["3045", "2885"], 3: ["500325"]}
    ids = [m["correlationID"] for m in server.control_messages]
    assert len(set(ids)) == 2 and all(len(i) == 10 for i in ids)


async def test_a_large_subscription_is_chunked_into_bounded_messages(
    server: FakeFeedServer,
) -> None:
    chain = Chain(server.url)
    chain.start()
    try:
        await server.wait_for(lambda: chain.observer.connected == 1)
        await chain.manager.subscribe([make_instrument(str(n)) for n in range(1, 151)])
        await server.wait_for(lambda: len(server.control_messages) == 2)
    finally:
        await chain.shutdown()

    sizes = [len(tokens_of(m)[1]) for m in server.control_messages]
    assert sizes == [100, 50]


async def test_a_reconnect_restores_the_full_active_watchlist_automatically(
    server: FakeFeedServer,
) -> None:
    """Acceptance: drop the socket; the client reconnects and the whole watchlist is resent."""
    chain = Chain(server.url)
    chain.start()
    try:
        await server.wait_for(lambda: chain.observer.connected == 1)
        await chain.manager.subscribe([make_instrument("1"), make_instrument("2")])
        await chain.manager.unsubscribe([make_instrument("1")])
        await chain.manager.subscribe([make_instrument("3")])
        await server.wait_for(lambda: len(server.control_messages) == 3)
        server.control_messages.clear()

        await server.drop_all()
        await server.wait_for(lambda: chain.observer.connected == 2)
        await server.wait_for(lambda: len(server.control_messages) == 1)
    finally:
        await chain.shutdown()

    (message,) = server.control_messages
    assert message["action"] == 1
    assert sorted(tokens_of(message)[1]) == ["2", "3"]  # exactly the live set; "1" stays gone


async def test_subscribing_beyond_the_cap_is_rejected_before_anything_is_sent(
    server: FakeFeedServer,
) -> None:
    chain = Chain(server.url, limit=3)
    chain.start()
    try:
        await server.wait_for(lambda: chain.observer.connected == 1)
        with pytest.raises(SubscriptionLimitExceededError):
            await chain.manager.subscribe([make_instrument(str(n)) for n in range(1, 5)])
        await asyncio.sleep(0.05)
    finally:
        await chain.shutdown()

    assert server.control_messages == [] and chain.manager.active == ()
