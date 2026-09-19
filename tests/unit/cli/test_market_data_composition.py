"""EM-54 end to end: real sockets, real binary frames, the fully wired market-data stack.

A fake feed server speaks the wire protocol. Frames flow socket -> parser -> queue -> normalizer
-> aggregator -> persister -> store; then the socket is dropped and we assert the client
reconnects, the WHOLE watchlist is resubscribed, and the gap is backfilled from history with the
bars flagged partial. (No live session was available — the top-level acceptance against real
broker candles is verified separately when the market is open.)"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest

from emporos.broker.backoff import BackoffPolicy
from emporos.cli.market_data_composition import MarketDataComposer, MarketDataStack
from emporos.core.clock import IST, FixedClock
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.instruments import Instrument
from emporos.domain.money import Money
from emporos.instruments.cache import InstrumentCache
from tests.support.fakes import (
    AdvancingSleeper,
    InMemoryCandleStore,
    RecordingAlertSink,
    make_instrument,
)
from tests.support.frames import FrameBuilder, epoch_ms
from tests.support.ws_doubles import FixedJitter, RecordingAuth
from tests.support.ws_server import FakeFeedServer

FRIDAY = date(2026, 9, 18)
SBIN, RELIANCE = make_instrument("3045"), make_instrument("2885")


def ist(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(
        FRIDAY.year, FRIDAY.month, FRIDAY.day, hour, minute, second, tzinfo=IST
    ).astimezone(UTC)


class History:
    def __init__(self) -> None:
        self.requests: list[tuple[str, datetime, datetime]] = []

    async def fetch_minutes(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> list[Candle]:
        self.requests.append((instrument.instrument_id, start, end))
        n = int((end - start) / timedelta(minutes=1))
        p = Money.of("500.00")
        return [
            Candle(
                instrument.instrument_id, Timeframe.M1, start + timedelta(minutes=i), p, p, p, p, 42
            )
            for i in range(n)
        ]


class Rig:
    def __init__(self, server: FakeFeedServer) -> None:
        self.server = server
        self.clock = FixedClock(ist(10, 0, 20))
        self.sleeper = AdvancingSleeper(self.clock)
        self.store = InMemoryCandleStore()
        self.history = History()
        self.alerts = RecordingAlertSink()
        self.stack: MarketDataStack = MarketDataComposer(
            auth=RecordingAuth(),
            resolver=InstrumentCache([SBIN, RELIANCE]),
            writer=self.store,
            backfill=self.history,
            clock=self.clock,
            sleeper=self.sleeper,
            jitter=FixedJitter(),
            alerts=self.alerts,
            url=server.url,
            reconnect_policy=BackoffPolicy(base_delay=0.01, max_delay=0.02, max_attempts=1),
        ).build()
        self.tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self.tasks = [
            asyncio.create_task(self.stack.client.run()),
            asyncio.create_task(self.stack.pipeline.run()),
        ]

    async def stop(self) -> None:
        await self.stack.client.stop()
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, 3)

    async def frames_processed(self, count: int) -> None:
        await self.server.wait_for(lambda: self.stack.reader.counts.parsed >= count)
        await self.server.wait_for(lambda: len(self.stack.queue) == 0)
        await asyncio.sleep(0.02)  # let the pipeline finish the tick it just dequeued

    async def m1(self, instrument: Instrument) -> list[Candle]:
        return await self.store.read(instrument.instrument_id, Timeframe.M1, ist(9, 0), ist(16, 0))


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async with FakeFeedServer().running() as server:
        harness = Rig(server)
        harness.start()
        try:
            yield harness
        finally:
            await harness.stop()


def quote(token: str, at: datetime, ltp_paise: int, volume: int, seq: int) -> bytes:
    return FrameBuilder.quote(
        token=token, ts_ms=epoch_ms(at), ltp_paise=ltp_paise, volume=volume, sequence=seq
    )


async def test_frames_become_persisted_candles_end_to_end(rig: Rig) -> None:
    await rig.server.wait_for(lambda: rig.stack.client.is_connected)
    await rig.stack.subscriptions.subscribe([SBIN])
    for seq, (second, paise, volume) in enumerate(
        [(2, 99620, 1000), (20, 99700, 1400), (50, 99580, 1900)], 1
    ):
        await rig.server.send_frame(quote("3045", ist(10, 0, second), paise, volume, seq))
    await rig.frames_processed(3)

    rig.clock.set(ist(10, 1, 2))  # the 10:00 minute plus grace has elapsed
    rig.stack.aggregator.advance()
    await rig.stack.persister.flush()

    (bar,) = await rig.m1(SBIN)
    assert bar.ts == ist(10, 0)
    assert (bar.open, bar.high, bar.low, bar.close) == (
        Money.of("996.20"), Money.of("997.00"), Money.of("995.80"), Money.of("995.80"),
    )  # fmt: skip
    assert bar.volume == 900 and bar.partial is True  # mid-session start: no volume baseline


async def test_the_reader_survives_garbage_and_out_of_scope_frames(rig: Rig) -> None:
    await rig.server.wait_for(lambda: rig.stack.client.is_connected)
    await rig.server.send_frame(b"\x01garbage")
    await rig.server.send_frame(FrameBuilder.depth())
    await rig.server.send_frame(quote("3045", ist(10, 0, 5), 99620, 10, 1))
    await rig.frames_processed(1)

    counts = rig.stack.reader.counts
    assert (counts.parsed, counts.malformed, counts.unsupported) == (1, 1, 1)
    assert rig.stack.client.is_connected  # the connection never faltered


async def test_a_reconnect_resubscribes_everything_and_backfills_the_gap_as_partial(
    rig: Rig,
) -> None:
    await rig.server.wait_for(lambda: rig.stack.client.is_connected)
    await rig.stack.subscriptions.subscribe([SBIN, RELIANCE])
    await rig.server.wait_for(lambda: len(rig.server.control_messages) == 1)
    rig.server.control_messages.clear()
    rig.clock.set(ist(10, 2, 10))  # the outage begins here

    await rig.server.drop_all()
    await rig.server.wait_for(lambda: len(rig.server.control_messages) == 1)  # the resubscribe
    await rig.stack.recovery.wait()

    (message,) = rig.server.control_messages
    tokens = [t for entry in message["params"]["tokenList"] for t in entry["tokens"]]
    assert message["action"] == 1 and sorted(tokens) == ["2885", "3045"]  # the FULL watchlist
    assert {r[0] for r in rig.history.requests} == {"NSE:3045", "NSE:2885"}  # both backfilled
    (report,) = rig.stack.recovery.reports
    assert report.failed == () and report.window_start == ist(10, 2)
    gap_bars = [b for b in await rig.m1(SBIN) if b.ts >= ist(10, 2)]
    assert gap_bars and all(b.partial for b in gap_bars)  # flagged, per plan §7


async def test_the_first_connection_triggers_no_backfill(rig: Rig) -> None:
    await rig.server.wait_for(lambda: rig.stack.client.is_connected)
    await rig.stack.recovery.wait()
    assert rig.history.requests == [] and rig.stack.recovery.reports == []
