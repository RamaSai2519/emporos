"""EM-61: the order-update stream — tolerant parsing, fan-out, and the real socket path."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from emporos.broker.angelone.mapping import AccountMapper
from emporos.broker.angelone.session import Session
from emporos.broker.angelone.ws_orders import (
    OrderUpdateHub,
    OrderUpdateParser,
    OrderUpdateReader,
    order_stream_client,
)
from emporos.broker.backoff import BackoffPolicy
from emporos.broker.models import BrokerOrderStatus, BrokerOrderUpdate
from emporos.core.clock import FixedClock, SystemClock
from emporos.domain.orders import OrderSide, OrderType
from tests.support.angelone_broker import NOW, session
from tests.support.ws_doubles import FixedJitter, RealSleeper, RecordingListener
from tests.support.ws_server import FakeFeedServer

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "angelone" / "streams"
UPDATE = json.loads((FIXTURE / "order_update_open.json").read_text())["message"]
PARSER = OrderUpdateParser(AccountMapper(), FixedClock(NOW))


def message(**data_overrides: Any) -> str:
    data = UPDATE["orderData"] | data_overrides
    return json.dumps({**UPDATE, "orderData": data})


def test_an_update_maps_to_the_order_as_it_now_stands_with_its_client_tag() -> None:
    update = PARSER.parse(json.dumps(UPDATE))

    assert update is not None and update.received_at == NOW
    order = update.order
    assert (order.broker_order_id, order.client_tag, order.instrument_id) == (
        "201020000000080", "ABC123", "NSE:3045",
    )  # fmt: skip
    assert (order.side, order.order_type, order.status) == (
        OrderSide.BUY, OrderType.LIMIT, BrokerOrderStatus.OPEN,
    )  # fmt: skip


def test_a_fill_update_reports_the_new_status_and_filled_quantity() -> None:
    update = PARSER.parse(message(orderstatus="complete", filledshares="10"))
    assert update is not None
    assert update.order.status is BrokerOrderStatus.FILLED and update.order.filled_quantity == 10


def test_the_streams_error_message_becomes_the_status_message_when_the_order_has_none() -> None:
    body = json.dumps(
        {
            **UPDATE,
            "error-message": "RMS:Margin Exceeds",
            "orderData": UPDATE["orderData"] | {"orderstatus": "rejected"},
        }
    )
    update = PARSER.parse(body)
    assert update is not None
    assert update.order.status is BrokerOrderStatus.REJECTED
    assert update.order.status_message == "RMS:Margin Exceeds"


@pytest.mark.parametrize(
    "text", ["", "not json", "[]", '"str"', '{"hello": "world"}', '{"orderData": null}']
)
def test_messages_that_are_not_order_updates_are_ignored_not_errors(text: str) -> None:
    assert PARSER.parse(text) is None


def test_the_greeting_recorded_live_on_connect_is_a_notice_not_a_malformed_update() -> None:
    greeting = json.loads((FIXTURE / "order_stream_greeting.json").read_text())["message"]
    reader = OrderUpdateReader(PARSER, OrderUpdateHub())

    assert PARSER.parse(json.dumps(greeting)) is None
    reader.on_text(json.dumps(greeting))

    assert (reader.received, reader.ignored, reader.malformed) == (0, 1, 0)


def test_a_message_that_claims_to_be_an_update_but_is_unusable_is_a_value_error() -> None:
    broken = json.dumps({"orderData": {"exchange": "NSE"}})  # no orderid, side, quantity...
    with pytest.raises(ValueError, match="unusable"):
        PARSER.parse(broken)
    with pytest.raises(ValueError, match="unusable"):
        PARSER.parse(message(transactiontype="SHORT"))


def test_the_reader_counts_publishes_and_survives_bad_input() -> None:
    hub, seen = OrderUpdateHub(), []
    hub.add_handler(seen.append)
    reader = OrderUpdateReader(PARSER, hub)

    for text in (json.dumps(UPDATE), "pong-ish", json.dumps({"orderData": {}}), json.dumps(UPDATE)):
        reader.on_text(text)  # none of these may raise

    assert (reader.received, reader.ignored, reader.malformed) == (2, 1, 1)
    assert len(seen) == 2


def test_a_failing_handler_never_starves_the_others() -> None:
    hub, seen = OrderUpdateHub(), []

    def broken(update: BrokerOrderUpdate) -> None:
        raise RuntimeError("bug")

    hub.add_handler(broken)
    hub.add_handler(seen.append)
    update = PARSER.parse(json.dumps(UPDATE))
    assert update is not None

    hub.publish(update)

    assert seen == [update]


class Provider:
    """`SessionProvider` double."""

    def __init__(self) -> None:
        self.renewed = 0

    async def session(self) -> Session:
        return session("1")

    async def renew(self, rejected: Session) -> Session:
        self.renewed += 1
        return session("2")


@pytest.fixture
async def server() -> AsyncIterator[FakeFeedServer]:
    async with FakeFeedServer().running() as running:
        yield running


async def test_updates_flow_over_a_real_socket_with_bearer_only_authentication(
    server: FakeFeedServer,
) -> None:
    hub, received = OrderUpdateHub(), []
    hub.add_handler(received.append)
    listener = RecordingListener()
    client = order_stream_client(
        Provider(),
        OrderUpdateReader(OrderUpdateParser(AccountMapper(), SystemClock()), hub),
        listener,
        SystemClock(),
        RealSleeper(),
        FixedJitter(),
        url=server.url,
        reconnect_policy=BackoffPolicy(base_delay=0.01, max_delay=0.02, max_attempts=1),
    )
    task = asyncio.create_task(client.run())
    try:
        await server.wait_for(lambda: listener.connected == 1)
        await server.send_text(json.dumps(UPDATE))
        await server.send_text("pong")  # the heartbeat reply is not an update
        await server.send_text(json.dumps({"notice": "hello"}))
        await server.wait_for(lambda: len(received) == 1)
    finally:
        await client.stop()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 3)

    headers = server.handshake_headers[0]
    assert headers["authorization"] == "Bearer jwt-1"
    assert "x-api-key" not in headers and "x-feed-token" not in headers  # bearer only
    assert received[0].order.client_tag == "ABC123"
    assert client.stats.unexpected_text == 0  # every text message went to the handler
