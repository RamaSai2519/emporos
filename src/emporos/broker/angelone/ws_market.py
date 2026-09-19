"""Our own SmartWebSocketV2 client (EM-48, Decision 4).

Unlike the SDK, whose socket runs with certificate verification switched off, TLS verification
is always on here: there is no parameter to turn it off, and a certificate failure is fatal
(retrying cannot fix a bad certificate, and it must never be "fixed" by trusting it).

Liveness follows Angel One's text protocol: the client sends `ping` every ~10s and the server
answers `pong`; a socket that stops answering is closed and re-established. Reconnects use
exponential backoff with jitter, ask for a refreshed feed token after an auth rejection, and
notify a `ConnectionListener` on every (re)connect so the subscription set can be restored.

This module moves bytes. It does not parse frames (`ws_frames`) or decide what to subscribe to
(`marketdata.subscriptions`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Protocol

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from emporos.broker.angelone.feed_auth import FeedAuthProvider
from emporos.broker.backoff import BackoffPolicy, JitterSource
from emporos.broker.errors import BrokerConnectionError, BrokerTlsError
from emporos.core.clock import Clock, Sleeper
from emporos.domain.instruments import Exchange

_LOG = logging.getLogger(__name__)

WS_URL = "wss://smartapisocket.angelone.in/smart-stream"
HEARTBEAT_INTERVAL_SECONDS = 10.0
HEARTBEAT_MESSAGE = "ping"
HEARTBEAT_REPLY = "pong"
_AUTH_REJECTED = frozenset({401, 403})

# Reconnect ceiling: keep trying for as long as the market is open, but never faster than 30s.
RECONNECT_POLICY = BackoffPolicy(base_delay=1.0, max_delay=30.0, max_attempts=1)


class FeedMode(IntEnum):
    """Only the modes v1 uses. DEPTH (4) is deliberately absent: it is NSE-only, 50 tokens per
    request, and out of scope, so it is unrepresentable rather than merely unused."""

    LTP = 1
    QUOTE = 2
    SNAP_QUOTE = 3


class ExchangeType(IntEnum):
    NSE_CM = 1
    BSE_CM = 3

    @classmethod
    def of(cls, exchange: Exchange) -> ExchangeType:
        return {Exchange.NSE: cls.NSE_CM, Exchange.BSE: cls.BSE_CM}[exchange]


class SubscriptionAction(IntEnum):
    UNSUBSCRIBE = 0
    SUBSCRIBE = 1


def subscription_message(
    action: SubscriptionAction,
    mode: FeedMode,
    tokens: Mapping[ExchangeType, Sequence[str]],
    correlation_id: str,
) -> str:
    """The JSON control message, exactly as SmartWebSocketV2 sends it."""
    token_list = [
        {"exchangeType": int(exchange), "tokens": list(batch)} for exchange, batch in tokens.items()
    ]
    return json.dumps(
        {
            "correlationID": correlation_id,
            "action": int(action),
            "params": {"mode": int(mode), "tokenList": token_list},
        }
    )


class FrameHandler(Protocol):
    def on_frame(self, frame: bytes) -> None: ...


class TextHandler(Protocol):
    """Receives text messages other than the heartbeat reply (the order stream's updates)."""

    def on_text(self, text: str) -> None: ...


class ConnectionListener(Protocol):
    async def on_connected(self) -> None:
        """Called after every successful (re)connect, with the socket ready for subscriptions."""
        ...

    async def on_disconnected(self, reason: str) -> None: ...


@dataclass(frozen=True)
class FeedStats:
    connects: int
    frames: int
    handler_errors: int
    unexpected_text: int
    heartbeat_timeouts: int
    pongs: int


class MarketFeedClient:
    def __init__(
        self,
        auth: FeedAuthProvider,
        handler: FrameHandler,
        listener: ConnectionListener,
        clock: Clock,
        sleeper: Sleeper,
        jitter: JitterSource,
        *,
        url: str = WS_URL,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        pong_timeout: float = 2.5 * HEARTBEAT_INTERVAL_SECONDS,
        reconnect_policy: BackoffPolicy = RECONNECT_POLICY,
        text_handler: TextHandler | None = None,
    ) -> None:
        self._text_handler = text_handler
        self._auth = auth
        self._handler = handler
        self._listeners: list[ConnectionListener] = [listener]
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter
        self._url = url
        self._heartbeat_interval = heartbeat_interval
        self._pong_timeout = pong_timeout
        self._policy = reconnect_policy
        self._socket: ClientConnection | None = None
        self._stopping = asyncio.Event()
        self._last_pong: datetime | None = None
        self._counters = {
            "connects": 0,
            "frames": 0,
            "handler_errors": 0,
            "text": 0,
            "hb": 0,
            "pongs": 0,
        }

    def add_listener(self, listener: ConnectionListener) -> None:
        """Also notify `listener` of connects/disconnects (in registration order). Needed because
        a listener such as the subscription manager itself depends on this client."""
        self._listeners.append(listener)

    @property
    def is_connected(self) -> bool:
        return self._socket is not None

    @property
    def last_pong(self) -> datetime | None:
        return self._last_pong

    @property
    def stats(self) -> FeedStats:
        c = self._counters
        return FeedStats(
            c["connects"], c["frames"], c["handler_errors"], c["text"], c["hb"], c["pongs"]
        )

    async def run(self) -> None:
        """Connect, serve, and reconnect until `stop()`. Raises only on a fatal TLS failure."""
        attempt = 0
        refresh_auth = False
        while not self._stopping.is_set():
            try:
                await self._serve_one_connection(refresh_auth)
                attempt, refresh_auth = 0, False
                reason = "connection closed"
            except BrokerTlsError:
                await self._notify_disconnected("tls verification failed")
                raise
            except InvalidStatus as error:
                reason = f"handshake rejected (HTTP {error.response.status_code})"
                refresh_auth = error.response.status_code in _AUTH_REJECTED
            except (OSError, WebSocketException, TimeoutError) as error:
                reason = f"{type(error).__name__}"
            if self._stopping.is_set():
                return
            await self._notify_disconnected(reason)
            delay = self._policy.delay(min(attempt, 30), self._jitter)
            attempt += 1
            _LOG.warning("market feed down (%s); reconnecting in %.1fs", reason, delay)
            await self._sleeper.sleep(delay)

    async def stop(self) -> None:
        self._stopping.set()
        if self._socket is not None:
            await self._socket.close()

    async def send_subscription(
        self,
        action: SubscriptionAction,
        mode: FeedMode,
        tokens: Mapping[ExchangeType, Sequence[str]],
        correlation_id: str,
    ) -> None:
        socket = self._socket
        if socket is None:
            raise BrokerConnectionError("market feed is not connected")
        await socket.send(subscription_message(action, mode, tokens, correlation_id))

    async def _serve_one_connection(self, refresh_auth: bool) -> None:
        auth = await self._auth.feed_auth(refresh=refresh_auth)
        try:
            connection = connect(
                self._url,
                additional_headers=auth.headers(),
                ssl=ssl.create_default_context() if self._url.startswith("wss") else None,
                ping_interval=None,  # liveness is Angel One's text ping/pong, not WS-level pings
                max_size=2**20,
            )
            async with connection as socket:
                await self._run_connected(socket)
        except ssl.SSLError as error:
            raise BrokerTlsError("market feed TLS verification failed") from error

    async def _run_connected(self, socket: ClientConnection) -> None:
        self._socket = socket
        self._last_pong = self._clock.now()
        self._counters["connects"] += 1
        heartbeat = asyncio.create_task(self._heartbeat(socket))
        try:
            for listener in self._listeners:
                await listener.on_connected()
            await self._read(socket)
        except ConnectionClosed:
            pass
        finally:
            self._socket = None
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
                await heartbeat

    async def _notify_disconnected(self, reason: str) -> None:
        for listener in self._listeners:
            await listener.on_disconnected(reason)

    async def _read(self, socket: ClientConnection) -> None:
        async for message in socket:
            if isinstance(message, bytes):
                self._counters["frames"] += 1
                self._deliver(message)
            elif message == HEARTBEAT_REPLY:
                self._counters["pongs"] += 1
                self._last_pong = self._clock.now()
            elif self._text_handler is not None:
                self._deliver_text(message)
            else:  # e.g. a JSON error reply to a bad subscription — noted, never fatal
                self._counters["text"] += 1
                _LOG.warning("unexpected text message from market feed (%d chars)", len(message))

    def _deliver(self, frame: bytes) -> None:
        try:
            self._handler.on_frame(frame)
        except Exception:  # the reader must survive any handler fault
            self._counters["handler_errors"] += 1
            _LOG.exception("frame handler failed; frame dropped")

    def _deliver_text(self, text: str) -> None:
        assert self._text_handler is not None
        try:
            self._text_handler.on_text(text)
        except Exception:  # the reader must survive any handler fault
            self._counters["handler_errors"] += 1
            _LOG.exception("text handler failed; message dropped")

    async def _heartbeat(self, socket: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            silent_for = (
                self._clock.now() - (self._last_pong or self._clock.now())
            ).total_seconds()
            if silent_for > self._pong_timeout:
                self._counters["hb"] += 1
                _LOG.warning("no pong for %.0fs; closing the market feed socket", silent_for)
                await socket.close(code=1011, reason="heartbeat timeout")
                return
            await socket.send(HEARTBEAT_MESSAGE)
