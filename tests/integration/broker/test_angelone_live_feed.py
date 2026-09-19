"""Live market-feed connection check (EM-48): TLS-verified connect, header auth, ping/pong.

Works outside market hours (it asserts no ticks). Skipped unless ANGELONE_* credentials are set.
One session per client code: see test_angelone_live_auth.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from emporos.broker.angelone.factory import AngelOneStack
from emporos.broker.angelone.feed_auth import SessionFeedAuthProvider
from emporos.broker.angelone.ws_market import MarketFeedClient
from emporos.broker.backoff import RandomJitter
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings

pytestmark = pytest.mark.integration


class DiscardingHandler:
    def on_frame(self, frame: bytes) -> None:
        del frame


class SilentListener:
    async def on_connected(self) -> None:
        return None

    async def on_disconnected(self, reason: str) -> None:
        del reason


async def test_the_real_feed_accepts_our_handshake_and_answers_heartbeats(
    angelone_stack: AngelOneStack, angelone_settings: Settings
) -> None:
    assert angelone_settings.angelone_api_key and angelone_settings.angelone_client_code
    auth = SessionFeedAuthProvider(
        angelone_stack.sessions,
        angelone_settings.angelone_api_key,
        angelone_settings.angelone_client_code,
    )
    client = MarketFeedClient(
        auth,
        DiscardingHandler(),
        SilentListener(),
        SystemClock(),
        AsyncioSleeper(),
        RandomJitter(),
        heartbeat_interval=0.5,
    )
    task = asyncio.create_task(client.run())
    try:
        for _ in range(100):  # up to ~10s for the handshake and two heartbeats
            if client.stats.pongs >= 2:
                break
            await asyncio.sleep(0.1)
        stats = client.stats
        handshake_ok = stats.connects >= 1
        heartbeat_ok = stats.pongs >= 2
        stayed_up = stats.heartbeat_timeouts == 0
    finally:
        await client.stop()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 5)

    assert handshake_ok
    assert heartbeat_ok
    assert stayed_up
