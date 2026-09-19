"""EM-48: the market-feed client against an in-process server speaking the real wire protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import pytest

from emporos.broker.angelone.feed_auth import FeedAuth
from emporos.broker.angelone.ws_market import (
    ExchangeType,
    FeedMode,
    MarketFeedClient,
    SubscriptionAction,
    subscription_message,
)
from emporos.broker.backoff import BackoffPolicy
from emporos.broker.errors import BrokerConnectionError, BrokerTlsError
from emporos.core.clock import SystemClock
from emporos.domain.instruments import Exchange
from tests.support.tls import SelfSignedTlsServer
from tests.support.ws_server import FakeFeedServer

FAST = BackoffPolicy(base_delay=0.02, max_delay=0.08, max_attempts=1)


class RecordingAuth:
    """`FeedAuthProvider` double: hands out FeedAuth and records whether a refresh was requested."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def feed_auth(self, *, refresh: bool) -> FeedAuth:
        self.calls.append(refresh)
        return FeedAuth("jwt-1", "key-1", "C1", "feed-2" if refresh else "feed-1")


class RecordingHandler:
    def __init__(self, fail_on: bytes | None = None) -> None:
        self.frames: list[bytes] = []
        self._fail_on = fail_on

    def on_frame(self, frame: bytes) -> None:
        if frame == self._fail_on:
            raise RuntimeError("handler bug")
        self.frames.append(frame)


class RecordingListener:
    def __init__(self) -> None:
        self.connected = 0
        self.disconnects: list[str] = []

    async def on_connected(self) -> None:
        self.connected += 1

    async def on_disconnected(self, reason: str) -> None:
        self.disconnects.append(reason)


class RealSleeper:
    """Sleeps for real (the socket I/O is real) but records every reconnect delay."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        await asyncio.sleep(seconds)


class FixedJitter:
    def fraction(self) -> float:
        return 0.0


class Rig:
    def __init__(self, url: str, **kwargs: object) -> None:
        self.auth = RecordingAuth()
        self.handler = RecordingHandler(kwargs.pop("fail_on", None))  # type: ignore[arg-type]
        self.listener = RecordingListener()
        self.sleeper = RealSleeper()
        self.client = MarketFeedClient(
            self.auth,
            self.handler,
            self.listener,
            SystemClock(),
            self.sleeper,
            FixedJitter(),
            url=url,
            reconnect_policy=FAST,
            **kwargs,  # type: ignore[arg-type]
        )
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


async def test_it_connects_and_authenticates_with_the_four_handshake_headers(
    server: FakeFeedServer,
) -> None:
    rig = Rig(server.url)
    rig.start()
    try:
        await server.wait_for(lambda: rig.listener.connected == 1)
    finally:
        await rig.shutdown()

    headers = server.handshake_headers[0]
    assert headers["authorization"] == "Bearer jwt-1"
    assert headers["x-api-key"] == "key-1"
    assert headers["x-client-code"] == "C1"
    assert headers["x-feed-token"] == "feed-1"
    assert rig.auth.calls == [False]


async def test_the_heartbeat_pings_and_pongs_keep_the_connection_alive(
    server: FakeFeedServer,
) -> None:
    rig = Rig(server.url, heartbeat_interval=0.02, pong_timeout=0.2)
    rig.start()
    try:
        await server.wait_for(lambda: server.pings >= 8)
        # eight heartbeats over ~0.16s with a 0.2s timeout: still one connection, never dropped
        assert rig.client.stats.connects == 1 and rig.client.stats.heartbeat_timeouts == 0
        assert rig.client.last_pong is not None
    finally:
        await rig.shutdown()


async def test_a_silent_peer_is_detected_and_replaced(server: FakeFeedServer) -> None:
    server.answer_pings = False
    rig = Rig(server.url, heartbeat_interval=0.02, pong_timeout=0.06)
    rig.start()
    try:
        await server.wait_for(lambda: rig.client.stats.heartbeat_timeouts >= 1)
        await server.wait_for(lambda: rig.client.stats.connects >= 2)  # and it came back
    finally:
        await rig.shutdown()


async def test_binary_frames_reach_the_handler_in_order_and_text_is_not_a_frame(
    server: FakeFeedServer,
) -> None:
    rig = Rig(server.url)
    rig.start()
    try:
        await server.wait_for(lambda: rig.listener.connected == 1)
        for n in range(5):
            await server.send_frame(bytes([n]) * 3)
        await server.wait_for(lambda: len(rig.handler.frames) == 5)
    finally:
        await rig.shutdown()

    assert rig.handler.frames == [bytes([n]) * 3 for n in range(5)]
    assert rig.client.stats.frames == 5


async def test_a_failing_handler_never_kills_the_reader(server: FakeFeedServer) -> None:
    rig = Rig(server.url, fail_on=b"bad")
    rig.start()
    try:
        await server.wait_for(lambda: rig.listener.connected == 1)
        for frame in (b"one", b"bad", b"two"):
            await server.send_frame(frame)
        await server.wait_for(lambda: len(rig.handler.frames) == 2)
    finally:
        await rig.shutdown()

    assert rig.handler.frames == [b"one", b"two"]
    assert rig.client.stats.handler_errors == 1 and rig.client.stats.connects == 1


async def test_a_forced_disconnect_reconnects_within_the_backoff_schedule(
    server: FakeFeedServer,
) -> None:
    rig = Rig(server.url)
    rig.start()
    try:
        await server.wait_for(lambda: rig.listener.connected == 1)
        await server.drop_all()
        await server.wait_for(lambda: rig.listener.connected == 2)
    finally:
        await rig.shutdown()

    assert rig.listener.disconnects  # the drop was reported
    assert rig.sleeper.delays[0] == pytest.approx(
        FAST.ceiling(0) / 2
    )  # jitter 0 -> half the ceiling
    assert rig.client.stats.connects == 2


async def test_repeated_failures_back_off_exponentially_up_to_the_cap() -> None:
    down = FakeFeedServer()
    await down.start()
    port = down.port
    await down.stop()  # nothing is listening any more: every attempt is refused
    rig = Rig(f"ws://127.0.0.1:{port}")
    rig.start()
    try:
        while len(rig.sleeper.delays) < 5:
            await asyncio.sleep(0.01)
    finally:
        await rig.shutdown()

    floors = [FAST.ceiling(i) / 2 for i in range(5)]  # jitter pinned to 0 -> half the ceiling
    assert rig.sleeper.delays[:5] == pytest.approx(floors)
    assert floors[-1] == FAST.max_delay / 2  # the cap was reached
    assert rig.listener.connected == 0


async def test_an_auth_rejection_makes_the_next_attempt_refresh_the_feed_token(
    server: FakeFeedServer,
) -> None:
    server.reject_handshakes = 1
    rig = Rig(server.url)
    rig.start()
    try:
        await server.wait_for(lambda: rig.listener.connected == 1)
    finally:
        await rig.shutdown()

    assert rig.auth.calls == [False, True]
    assert server.handshake_headers[-1]["x-feed-token"] == "feed-2"  # the refreshed token was used
    assert any("401" in reason for reason in rig.listener.disconnects)


async def test_subscription_messages_match_the_smartwebsocketv2_wire_format(
    server: FakeFeedServer,
) -> None:
    rig = Rig(server.url)
    rig.start()
    try:
        await server.wait_for(lambda: rig.listener.connected == 1)
        await rig.client.send_subscription(
            SubscriptionAction.SUBSCRIBE,
            FeedMode.QUOTE,
            {ExchangeType.NSE_CM: ["3045", "2885"], ExchangeType.BSE_CM: ["500325"]},
            "abc123",
        )
        await server.wait_for(lambda: len(server.control_messages) == 1)
    finally:
        await rig.shutdown()

    assert server.control_messages[0] == {
        "correlationID": "abc123",
        "action": 1,
        "params": {
            "mode": 2,
            "tokenList": [
                {"exchangeType": 1, "tokens": ["3045", "2885"]},
                {"exchangeType": 3, "tokens": ["500325"]},
            ],
        },
    }


async def test_subscribing_while_disconnected_is_a_retryable_error() -> None:
    rig = Rig("ws://127.0.0.1:9")
    with pytest.raises(BrokerConnectionError):
        await rig.client.send_subscription(
            SubscriptionAction.SUBSCRIBE, FeedMode.LTP, {ExchangeType.NSE_CM: ["1"]}, "x"
        )


def test_only_the_v1_modes_and_cash_exchanges_are_representable() -> None:
    assert {m.name for m in FeedMode} == {"LTP", "QUOTE", "SNAP_QUOTE"}  # DEPTH is unrepresentable
    assert ExchangeType.of(Exchange.NSE) is ExchangeType.NSE_CM
    assert ExchangeType.of(Exchange.BSE) is ExchangeType.BSE_CM
    assert (
        json.loads(
            subscription_message(
                SubscriptionAction.UNSUBSCRIBE, FeedMode.LTP, {ExchangeType.NSE_CM: ["1"]}, "c"
            )
        )["action"]
        == 0
    )


async def test_stop_ends_run_cleanly_and_closes_the_socket(server: FakeFeedServer) -> None:
    rig = Rig(server.url)
    rig.start()
    await server.wait_for(lambda: rig.listener.connected == 1)

    await rig.client.stop()
    assert rig.task is not None
    await asyncio.wait_for(rig.task, 3)

    assert not rig.client.is_connected
    await server.wait_for(lambda: server.connection_count == 0)


async def test_tls_verification_is_fatal_and_never_retried() -> None:
    """A self-signed certificate must be refused, and `run()` must give up rather than loop."""
    tls_server = SelfSignedTlsServer()
    async with tls_server.running() as https_url:
        rig = Rig(https_url.replace("https://", "wss://"))
        with pytest.raises(BrokerTlsError):
            await asyncio.wait_for(rig.client.run(), 5)

    assert rig.listener.connected == 0
    assert rig.sleeper.delays == []  # no retry loop
    assert tls_server.connections_completed == 0
    assert rig.listener.disconnects == ["tls verification failed"]
