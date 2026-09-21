"""EM-142: `LiveVenue` wires session and order-feed health correctly, and the opener refuses
without credentials before it builds anything (no network, no Mongo)."""

from __future__ import annotations

import asyncio

import pytest

from emporos.broker.backoff import RandomJitter
from emporos.cli.live_feed import FeedRequest
from emporos.cli.live_venue import AngelOneLiveVenueOpener, LiveVenue, _OrderFeedHealth
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Timeframe
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.session import SessionWindow
from emporos.session.bar_feed import ClosedBarQueue
from emporos.session.risk_facts import VenueHealth
from emporos.session.worker import SessionVenue
from tests.support.in_memory_broker import InMemoryBroker


class _FakeSocket:
    def __init__(self) -> None:
        self.ran = False
        self.stopped = False

    async def run(self) -> None:
        self.ran = True

    async def stop(self) -> None:
        self.stopped = True


async def test_authenticate_marks_the_session_healthy() -> None:
    health = VenueHealth()
    venue: SessionVenue = LiveVenue(
        InMemoryBroker([], []), _FakeSocket(), _FakeSocket(), [], health
    )

    await venue.authenticate()

    assert health.session_ok() is True
    assert health.order_feed_ok() is False  # the order socket has not connected yet


async def test_the_order_feed_listener_is_the_only_thing_that_may_say_it_is_up() -> None:
    health = VenueHealth()
    listener = _OrderFeedHealth(health)

    await listener.on_connected()
    assert health.order_feed_ok() is True

    await listener.on_disconnected("socket closed")
    assert health.order_feed_ok() is False


async def test_connect_subscribes_market_data_and_starts_both_sockets() -> None:
    broker = InMemoryBroker([], [])
    market, orders = _FakeSocket(), _FakeSocket()
    venue = LiveVenue(broker, market, orders, [], VenueHealth())

    await venue.connect()
    await asyncio.sleep(0)  # the sockets run as background tasks; give them one loop turn

    assert market.ran is True
    assert orders.ran is True

    await venue.close()


async def test_close_marks_both_kinds_of_health_down_and_stops_the_sockets() -> None:
    health = VenueHealth()
    health.set(session=True, feed=True)
    market, orders = _FakeSocket(), _FakeSocket()
    venue = LiveVenue(InMemoryBroker([], []), market, orders, [], health)

    await venue.close()

    assert health.session_ok() is False
    assert health.order_feed_ok() is False
    assert market.stopped is True
    assert orders.stopped is True


async def test_no_credentials_means_no_live_venue() -> None:
    settings = Settings.model_construct(angelone_api_key=None, angelone_client_code=None)
    opener = AngelOneLiveVenueOpener(settings, SystemClock(), AsyncioSleeper(), RandomJitter())
    request = FeedRequest(
        database=None,  # type: ignore[arg-type]
        instruments=InstrumentCache(),
        master=None,  # type: ignore[arg-type]
        bars=ClosedBarQueue(frozenset({Timeframe.M5})),
        window=SessionWindow(),
    )

    with pytest.raises(ConfigurationError, match="ANGELONE_API_KEY"):
        async with opener.open(request, []):
            pytest.fail("a live venue was opened without credentials")  # pragma: no cover
