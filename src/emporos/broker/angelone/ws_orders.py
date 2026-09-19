"""The order-update stream (`wss://tns.angelone.in/smart-order-update`, plan.md §1.6).

It reuses `MarketFeedClient` for the socket (TLS verified, ping/pong liveness, backoff reconnect)
and only adds what differs: bearer-only handshake auth and JSON text messages instead of binary
frames. A pushed update carries the order's state including its `ordertag`, which is how
Decision 7 correlates an update with an intent whose HTTP reply was lost.

The payload schema is from plan §1.6 and SmartAPI's docs; the SDK only logs messages, and no real
update has been observed (no orders are placed from the dev key). Parsing is therefore tolerant of
extra fields and a malformed message is counted and dropped, never fatal.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import ValidationError

from emporos.broker.angelone.mapping import AccountMapper, order_from_update_data
from emporos.broker.angelone.session_manager import SessionProvider
from emporos.broker.angelone.ws_market import (
    HEARTBEAT_INTERVAL_SECONDS,
    RECONNECT_POLICY,
    ConnectionListener,
    MarketFeedClient,
)
from emporos.broker.backoff import BackoffPolicy, JitterSource
from emporos.broker.errors import BrokerError
from emporos.broker.models import BrokerOrderUpdate
from emporos.core.clock import Clock, Sleeper

_LOG = logging.getLogger(__name__)
ORDER_WS_URL = "wss://tns.angelone.in/smart-order-update"


@dataclass(frozen=True)
class OrderStreamAuth:
    jwt: str = field(repr=False)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt}"}


class OrderStreamAuthProvider:
    def __init__(self, sessions: SessionProvider) -> None:
        self._sessions = sessions

    async def feed_auth(self, *, refresh: bool) -> OrderStreamAuth:
        session = await self._sessions.session()
        if refresh:
            session = await self._sessions.renew(session)
        return OrderStreamAuth(session.jwt)


class OrderUpdateHub:
    """Fans updates out to registered handlers; one failing handler never starves the rest."""

    def __init__(self) -> None:
        self._handlers: list[Callable[[BrokerOrderUpdate], None]] = []

    def add_handler(self, handler: Callable[[BrokerOrderUpdate], None]) -> None:
        self._handlers.append(handler)

    def publish(self, update: BrokerOrderUpdate) -> None:
        for handler in self._handlers:
            try:
                handler(update)
            except Exception:
                _LOG.exception("order-update handler failed")


class OrderUpdateParser:
    def __init__(self, mapper: AccountMapper, clock: Clock) -> None:
        self._mapper = mapper
        self._clock = clock

    def parse(self, text: str) -> BrokerOrderUpdate | None:
        """The update, or `None` for a message that is not an order update (an ack, a notice).
        Raises `ValueError` for something that claims to be one but is unusable."""
        try:
            message: Any = json.loads(text)
        except ValueError:
            return None
        data = message.get("orderData") if isinstance(message, dict) else None
        if not isinstance(data, dict):
            return None
        try:
            order = order_from_update_data(data, self._mapper)
        except (ValidationError, BrokerError, ValueError) as error:
            raise ValueError(f"unusable order update ({type(error).__name__})") from None
        error_message = str(message.get("error-message") or "")
        if error_message and not order.status_message:
            order = replace(order, status_message=error_message)
        return BrokerOrderUpdate(order=order, received_at=self._clock.now())


class OrderUpdateReader:
    """The socket's `TextHandler`: parse, publish, count and drop what cannot be read."""

    def __init__(self, parser: OrderUpdateParser, hub: OrderUpdateHub) -> None:
        self._parser = parser
        self._hub = hub
        self.received = 0
        self.malformed = 0
        self.ignored = 0

    def on_text(self, text: str) -> None:
        try:
            update = self._parser.parse(text)
        except ValueError:
            self.malformed += 1
            _LOG.warning("dropping an unusable order update")
            return
        if update is None:
            self.ignored += 1
            return
        self.received += 1
        self._hub.publish(update)


class _NoFrames:
    def on_frame(self, frame: bytes) -> None:
        del frame  # the order stream is text-only


def order_stream_client(
    sessions: SessionProvider,
    reader: OrderUpdateReader,
    listener: ConnectionListener,
    clock: Clock,
    sleeper: Sleeper,
    jitter: JitterSource,
    *,
    url: str = ORDER_WS_URL,
    reconnect_policy: BackoffPolicy = RECONNECT_POLICY,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> MarketFeedClient:
    return MarketFeedClient(
        OrderStreamAuthProvider(sessions),
        _NoFrames(),
        listener,
        clock,
        sleeper,
        jitter,
        url=url,
        reconnect_policy=reconnect_policy,
        heartbeat_interval=heartbeat_interval,
        pong_timeout=2.5 * heartbeat_interval,
        text_handler=reader,
    )
