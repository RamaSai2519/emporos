"""EM-186: record a bounded sample of REAL market-feed frames, exercise one controlled socket drop,
and pull the broker's own 1m/5m history for the same instruments. READ-ONLY: market data only, no
order endpoint is ever touched. Run only with no other worker holding the account's one session.

    pipenv run python scripts/record_angelone_frames.py OUT.json [SECONDS] [DROP_AT_SECONDS]
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import sys
from datetime import UTC, datetime
from typing import Any

from emporos.broker.angelone.api import AngelOneApi, CandleInterval
from emporos.broker.angelone.factory import AngelOneStackFactory
from emporos.broker.angelone.feed_auth import SessionFeedAuthProvider
from emporos.broker.angelone.ws_market import (
    ExchangeType,
    FeedMode,
    MarketFeedClient,
    SubscriptionAction,
)
from emporos.broker.backoff import RandomJitter
from emporos.core.clock import IST, AsyncioSleeper, SystemClock
from emporos.core.config import Settings

TOKENS = {"3499": "TATASTEEL-EQ", "2885": "RELIANCE-EQ", "3045": "SBIN-EQ", "1594": "INFY-EQ"}


class Recorder:
    def __init__(self, clock: SystemClock) -> None:
        self._clock = clock
        self.frames: list[dict[str, str]] = []
        self.events: list[dict[str, str]] = []
        self.client: MarketFeedClient | None = None

    def on_frame(self, frame: bytes) -> None:
        self.frames.append(
            {"arrival_utc": self._clock.now().isoformat(), "b64": base64.b64encode(frame).decode()}
        )

    def _event(self, name: str, detail: str = "") -> None:
        self.events.append(
            {"at_utc": self._clock.now().isoformat(), "event": name, "detail": detail}
        )

    async def on_connected(self) -> None:
        self._event("connected")
        assert self.client is not None
        await self.client.send_subscription(
            SubscriptionAction.SUBSCRIBE,
            FeedMode.QUOTE,
            {ExchangeType.NSE_CM: list(TOKENS)},
            "0000000001",
        )
        self._event("subscribed", ",".join(TOKENS))

    async def on_disconnected(self, reason: str) -> None:
        self._event("disconnected", reason)


async def main(out: str, seconds: float, drop_at: float) -> None:
    clock = SystemClock()
    settings = Settings()
    stack = AngelOneStackFactory(settings, clock, AsyncioSleeper(), RandomJitter()).build()
    rec = Recorder(clock)
    assert settings.angelone_api_key and settings.angelone_client_code
    client = MarketFeedClient(
        SessionFeedAuthProvider(
            stack.sessions, settings.angelone_api_key, settings.angelone_client_code
        ),
        rec,
        rec,
        clock,
        AsyncioSleeper(),
        RandomJitter(),
    )
    rec.client = client
    runner = asyncio.create_task(client.run())
    try:
        await asyncio.sleep(drop_at)
        pongs_before = client.stats.pongs
        rec._event("controlled_drop", f"pongs_before={pongs_before}")
        sock = client._socket  # tooling only: a client-side close, the account session is untouched
        if sock is not None:
            await sock.close(code=1001, reason="EM-186 controlled drop")
        await asyncio.sleep(max(0.0, seconds - drop_at))
    finally:
        stats = client.stats
        await client.stop()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
    history: dict[str, Any] = {}
    api = AngelOneApi(stack.transport)
    today = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
    end = datetime.now(IST)
    for token in TOKENS:
        for interval in (CandleInterval.ONE_MINUTE, CandleInterval.FIVE_MINUTE):
            bars = await api.candles("NSE", token, interval, today, end)
            history[f"{token}:{interval.value}"] = [
                [
                    b.ts.astimezone(UTC).isoformat(),
                    str(b.open),
                    str(b.high),
                    str(b.low),
                    str(b.close),
                    b.volume,
                ]
                for b in bars
            ]
            await asyncio.sleep(0.5)
    with contextlib.suppress(Exception):
        await stack.sessions.logout()
    await stack.aclose()
    with open(out, "w") as fh:
        json.dump(
            {
                "recorded_utc": clock.now().isoformat(),
                "stats": stats.__dict__ if hasattr(stats, "__dict__") else str(stats),
                "events": rec.events,
                "frames": rec.frames,
                "history": history,
            },
            fh,
        )
    print("frames", len(rec.frames), "events", len(rec.events))


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1],
            float(sys.argv[2]) if len(sys.argv) > 2 else 240,
            float(sys.argv[3]) if len(sys.argv) > 3 else 90,
        )
    )
