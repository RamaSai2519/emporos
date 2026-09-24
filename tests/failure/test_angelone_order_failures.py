"""EM-186: broker failure paths on the ORDER endpoints, through the real Angel One stack.

Nothing here reaches a network. The fault-injecting HTTP server (`ScriptedHttpServer`, an httpx
transport) plays the broker's side of the wire, so the real classifier, retry policy, rate limiter,
session layer, request mapper and `AngelOneBroker` adapter all run. What is asserted is the
platform's safety contract for money-moving calls: a definitive rejection is surfaced once, an
ambiguous outcome is never resent (it is resolved by tag), a rate-limit denial and an expired
session never turn into a duplicate order, and a dropped order-update socket is noticed and
resubscribed. The same behaviours are UNVERIFIED against the real endpoint (BLOCKED: no order has
been attempted from the registered host).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.authenticated import AuthenticatedTransport
from emporos.broker.angelone.limits import ANGELONE_RATE_LIMITS
from emporos.broker.angelone.mapping import AccountMapper
from emporos.broker.angelone.retry import RetryingTransport
from emporos.broker.angelone.session import Session
from emporos.broker.angelone.throttling import RateLimitedTransport
from emporos.broker.angelone.transport import HttpRestTransport
from emporos.broker.angelone.ws_orders import (
    OrderUpdateHub,
    OrderUpdateParser,
    OrderUpdateReader,
    order_stream_client,
)
from emporos.broker.backoff import BackoffPolicy
from emporos.broker.errors import (
    BrokerRateLimitedError,
    BrokerRejectedError,
    BrokerSessionExpiredError,
    BrokerTransportError,
)
from emporos.broker.models import BrokerOrderStatus, BrokerOrderUpdate
from emporos.broker.ratelimit import GroupRateLimiter
from emporos.core.clock import FixedClock, SystemClock
from emporos.core.errors import ErrorClassification
from tests.support.angelone_broker import (
    SBIN,
    BrokerRig,
    RecordingCandleFetcher,
    RecordingMarketData,
    StubCatalog,
    StubSessions,
    session,
)
from tests.support.fakes import (
    AdvancingSleeper,
    FixedJitter,
    ScriptedHttpServer,
    failed_reply,
    ok_reply,
)
from tests.support.ws_doubles import FixedJitter as WsJitter
from tests.support.ws_doubles import RealSleeper, RecordingListener
from tests.support.ws_server import FakeFeedServer
from tests.unit.broker.angelone.test_adapter import order_row, place

RATE_LIMIT_TEXT = "Access denied because of exceeding access rate"
TAG = "ABC123"


class SessionDouble:
    """`SessionProvider` that counts renewals: what the session layer would do on expiry."""

    def __init__(self) -> None:
        self.renewals = 0

    async def session(self) -> Session:
        return session("1")

    async def renew(self, rejected: Session) -> Session:
        self.renewals += 1
        return session(str(self.renewals + 1))


class Stack:
    """The real order path over a fault-injecting HTTP server."""

    def __init__(self) -> None:
        clock = FixedClock(datetime(2026, 9, 24, 4, 30, tzinfo=UTC))
        self.sleeper = AdvancingSleeper(clock)
        self.server = ScriptedHttpServer()
        self.provider = SessionDouble()
        raw = RetryingTransport(
            RateLimitedTransport(
                HttpRestTransport(self.server.client(), "key"),
                GroupRateLimiter(ANGELONE_RATE_LIMITS, clock, self.sleeper),
            ),
            self.sleeper,
            FixedJitter(0.5),
        )
        rig = BrokerRig()
        from emporos.broker.angelone.adapter import AngelOneBroker
        from emporos.instruments.cache import InstrumentCache

        self.broker = AngelOneBroker(
            AngelOneApi(AuthenticatedTransport(raw, self.provider)),
            StubSessions(),
            "A0000000",
            InstrumentCache([SBIN]),
            StubCatalog([SBIN]),
            RecordingCandleFetcher(),
            RecordingMarketData(),
            rig.ticks,
            rig.updates,
        )

    def paths(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.server.requests]

    def wire_tags(self) -> list[str]:
        return [
            json.loads(r.content)["ordertag"]
            for r in self.server.requests
            if "placeOrder" in r.url.path
        ]


async def test_a_rejected_order_is_surfaced_once_and_never_retried() -> None:
    stack = Stack()
    stack.server.queue(failed_reply("Insufficient funds", code="AB1007"))

    with pytest.raises(BrokerRejectedError) as raised:
        await stack.broker.place_order(place())

    assert raised.value.classification is ErrorClassification.DEFINITIVE
    assert stack.paths() == ["placeOrder"] and stack.provider.renewals == 0


async def test_a_timed_out_placement_is_unknown_and_resolved_by_tag_never_resent() -> None:
    stack = Stack()
    stack.server.queue(
        httpx.ReadTimeout("the reply never came"),
        ok_reply([order_row("OTHER1", "199"), order_row(TAG, "201")]),
    )

    with pytest.raises(BrokerTransportError) as raised:
        await stack.broker.place_order(place())
    assert raised.value.classification is ErrorClassification.AMBIGUOUS

    found = await stack.broker.find_orders_by_tag(TAG)

    assert [o.broker_order_id for o in found] == ["201"]
    assert stack.paths() == ["placeOrder", "getOrderBook"]  # one placement, then a lookup
    assert stack.wire_tags() == [TAG]  # the audit identifier on the wire is the client's own tag


async def test_a_connection_reset_after_sending_is_also_never_resent() -> None:
    stack = Stack()
    stack.server.queue(httpx.ReadError("connection reset by peer"))

    with pytest.raises(BrokerTransportError) as raised:
        await stack.broker.place_order(place())

    assert raised.value.classification is ErrorClassification.AMBIGUOUS
    assert stack.paths() == ["placeOrder"]


async def test_a_plain_text_403_rate_limit_on_a_placement_is_not_replayed_unboundedly() -> None:
    """The 403 plain-text denial (memory: observed live) is classified as a rate limit. Whatever
    the policy does with it, the number of placements on the wire stays within the retry budget and
    the end result is a typed rate-limit error, not a silent success or an unbounded loop."""
    stack = Stack()
    denial = httpx.Response(403, text=RATE_LIMIT_TEXT)
    stack.server.queue(*[httpx.Response(403, text=RATE_LIMIT_TEXT) for _ in range(6)], denial)

    with pytest.raises(BrokerRateLimitedError):
        await stack.broker.place_order(place())

    assert 1 <= stack.paths().count("placeOrder") <= 6
    assert set(stack.paths()) == {"placeOrder"}


async def test_an_expired_session_on_a_placement_is_not_replayed_with_a_new_token() -> None:
    stack = Stack()
    stack.server.queue(
        httpx.Response(
            200,
            content=json.dumps(
                {"data": "", "errorCode": "AG8001", "message": "Invalid Token", "success": False}
            ),
        )
    )

    with pytest.raises(BrokerSessionExpiredError):
        await stack.broker.place_order(place())

    assert stack.paths() == ["placeOrder"]  # exactly one attempt: a replay could double the order


async def test_a_rejected_token_on_a_read_is_renewed_and_replayed_once() -> None:
    stack = Stack()
    invalid = {"data": "", "errorCode": "AG8001", "message": "Invalid Token", "success": False}
    stack.server.queue(httpx.Response(200, content=json.dumps(invalid)), ok_reply([]))

    assert await stack.broker.find_orders_by_tag(TAG) == []

    assert stack.paths() == ["getOrderBook", "getOrderBook"] and stack.provider.renewals == 1


async def test_an_unrecognised_cancel_failure_is_treated_as_ambiguous_and_never_replayed() -> None:
    """Conservative by design: an error code the classifier does not know cannot be proven to mean
    "not executed", so it is AMBIGUOUS (to be resolved against the order book), not DEFINITIVE."""
    from emporos.broker.models import CancelOrderRequest
    from emporos.domain.orders import OrderType

    stack = Stack()
    stack.server.queue(failed_reply("Order already completed", code="AB2001"))

    with pytest.raises(BrokerTransportError) as raised:
        await stack.broker.cancel_order(CancelOrderRequest("201", OrderType.LIMIT))

    assert raised.value.classification is ErrorClassification.AMBIGUOUS
    assert stack.paths() == ["cancelOrder"]


UPDATE_OPEN = json.loads(
    (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "fixtures/angelone/streams/order_update_open.json"
    ).read_text()
)["message"]


async def test_a_dropped_order_update_socket_reconnects_and_later_updates_still_arrive() -> None:
    """WS drop mid-order: updates before and after the drop are both delivered, and the listener is
    told about the reconnect (which is what triggers reconciliation by tag)."""
    hub, received = OrderUpdateHub(), []
    hub.add_handler(received.append)
    listener = RecordingListener()
    async with FakeFeedServer().running() as server:
        client = order_stream_client(
            SessionDouble(),
            OrderUpdateReader(OrderUpdateParser(AccountMapper(), SystemClock()), hub),
            listener,
            SystemClock(),
            RealSleeper(),
            WsJitter(),
            url=server.url,
            reconnect_policy=BackoffPolicy(base_delay=0.01, max_delay=0.02, max_attempts=1),
        )
        task = asyncio.create_task(client.run())
        try:
            await server.wait_for(lambda: listener.connected == 1)
            await server.send_text(json.dumps(UPDATE_OPEN))
            await server.wait_for(lambda: len(received) == 1)
            await server.drop_all()  # the socket dies while the order is still working
            await server.wait_for(lambda: listener.connected == 2)
            filled = UPDATE_OPEN["orderData"] | {"orderstatus": "complete", "filledshares": "10"}
            await server.send_text(json.dumps({**UPDATE_OPEN, "orderData": filled}))
            await server.wait_for(lambda: len(received) == 2)
        finally:
            await client.stop()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, 3)

    assert listener.disconnects, "the drop was not reported to the listener"
    assert [u.order.client_tag for u in received] == ["ABC123", "ABC123"]
    assert received[1].order.status is BrokerOrderStatus.FILLED
    assert isinstance(received[0], BrokerOrderUpdate)
