"""Live, READ-ONLY checks of `AngelOneBroker` against the real account, and the order-update socket
handshake. No order is ever placed, modified or cancelled (orders are IP-gated to the production
host's registered static IP, and tests/unit/test_order_safety.py forbids this file from naming
those operations).

Assertions are on derived booleans so a failure cannot echo account data. Skipped unless ANGELONE_*
credentials are set. One session per client code: see test_angelone_live_auth."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest

from emporos.broker.angelone.adapter import AngelOneBroker
from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.factory import AngelOneStack
from emporos.broker.angelone.mapping import AccountMapper
from emporos.broker.angelone.ws_orders import (
    OrderUpdateHub,
    OrderUpdateParser,
    OrderUpdateReader,
    order_stream_client,
)
from emporos.broker.backoff import RandomJitter
from emporos.broker.models import CandleRequest
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from emporos.instruments.cache import InstrumentCache
from tests.support.angelone_broker import HandlerList, RecordingMarketData, StubCatalog
from tests.support.fakes import make_instrument

pytestmark = pytest.mark.integration

SBIN = make_instrument("3045", symbol="SBIN-EQ")


@pytest.fixture
def broker(angelone_stack: AngelOneStack, angelone_settings: Settings) -> AngelOneBroker:
    assert angelone_settings.angelone_client_code
    return AngelOneBroker(
        AngelOneApi(angelone_stack.transport),
        angelone_stack.sessions,
        angelone_settings.angelone_client_code,
        InstrumentCache([SBIN]),
        StubCatalog([SBIN]),
        AngelOneCandleBackfill(AngelOneApi(angelone_stack.transport)),
        RecordingMarketData(),
        HandlerList(),
        HandlerList(),
    )


async def test_the_read_only_broker_surface_works_against_the_real_account(
    broker: AngelOneBroker,
) -> None:
    session = await broker.ensure_session()
    profile = await broker.get_profile()
    funds = await broker.get_funds()
    book, trades = await broker.get_order_book(), await broker.get_trade_book()
    positions, holdings = await broker.get_positions(), await broker.get_holdings()
    missing = await broker.find_orders_by_tag("NOSUCHTAG1")

    assert session.expires_at > session.established_at
    assert profile.exchanges and any(e.value == "NSE" for e in profile.exchanges)
    assert isinstance(funds.net, Money) and isinstance(funds.available_cash, Money)
    all_lists = all(isinstance(x, list) for x in (book, trades, positions, holdings))
    assert all_lists  # an empty account lists as empty (the API answers `null`), never as an error
    assert missing == []


async def test_quotes_and_history_map_to_exact_neutral_types(broker: AngelOneBroker) -> None:
    (quote,) = await broker.get_quote(["NSE:3045"])
    end = datetime.now(UTC)
    bars = await broker.get_historical_candles(
        CandleRequest("NSE:3045", Timeframe.D1, end - timedelta(days=10), end)
    )

    within_circuits = quote.lower_circuit is not None and quote.lower_circuit < quote.ltp
    assert within_circuits and quote.instrument_id == "NSE:3045"
    assert bars and all(b.instrument_id == "NSE:3045" for b in bars)
    assert all(isinstance(b.close, Money) for b in bars)


async def test_the_real_order_update_socket_accepts_our_handshake_and_answers_heartbeats(
    angelone_stack: AngelOneStack,
) -> None:
    class Quiet:
        async def on_connected(self) -> None:
            return None

        async def on_disconnected(self, reason: str) -> None:
            del reason

    client = order_stream_client(
        angelone_stack.sessions,
        OrderUpdateReader(OrderUpdateParser(AccountMapper(), SystemClock()), OrderUpdateHub()),
        Quiet(),
        SystemClock(),
        AsyncioSleeper(),
        RandomJitter(),
        heartbeat_interval=0.5,
    )
    task = asyncio.create_task(client.run())
    try:
        for _ in range(100):
            if client.stats.pongs >= 1:
                break
            await asyncio.sleep(0.1)
        stats = client.stats
        connected, alive = stats.connects >= 1, stats.heartbeat_timeouts == 0
        heartbeat = stats.pongs >= 1
    finally:
        await client.stop()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 5)

    assert connected
    assert alive
    assert heartbeat
