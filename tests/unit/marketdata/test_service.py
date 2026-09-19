"""The service runs the pipeline, minute scheduler and watchdog together and stops them cleanly."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from emporos.core.clock import AsyncioSleeper, FixedClock
from emporos.instruments.cache import InstrumentCache
from emporos.marketdata.aggregator import MinuteCandleAggregator
from emporos.marketdata.candle_writer import CandlePersister
from emporos.marketdata.normalizer import TickNormalizer
from emporos.marketdata.pipeline import TickPipeline
from emporos.marketdata.queue import BoundedTickQueue
from emporos.marketdata.scheduler import MinuteScheduler
from emporos.marketdata.service import MarketDataService
from emporos.marketdata.staleness import StalenessWatchdog
from tests.support.fakes import InMemoryCandleStore, RecordingAlertSink, make_instrument, raw_tick

T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)  # 10:00 IST, in session


async def test_all_three_loops_run_and_stop_cleanly() -> None:
    clock, alerts = FixedClock(T0), RecordingAlertSink()
    queue = BoundedTickQueue(clock)
    aggregator = MinuteCandleAggregator(clock)
    watchdog = StalenessWatchdog(clock, alerts=alerts)
    watchdog.watch(["NSE:2885"])  # never ticks: the watchdog loop must notice
    persister = CandlePersister(InMemoryCandleStore())
    pipeline = TickPipeline(
        queue, TickNormalizer(InstrumentCache([make_instrument("3045")])), (aggregator, watchdog)
    )
    service = MarketDataService(
        pipeline,
        MinuteScheduler(aggregator, persister, clock, AsyncioSleeper(), aggregator.grace),
        watchdog,
        AsyncioSleeper(),
        watchdog_interval=0.01,
    )

    service.start()
    service.start()  # idempotent
    queue.on_raw_tick(raw_tick("3045", at=T0))
    for _ in range(100):
        if watchdog.silent_for("NSE:3045") is not None and any(
            name == "market_data.stale" for name, _ in alerts.alerts
        ):
            break
        await asyncio.sleep(0.01)
    running_before = service.running
    await service.stop()

    assert running_before and not service.running
    assert not watchdog.is_stale("NSE:3045")  # the tick was consumed by the pipeline
    assert any(name == "market_data.stale" for name, _ in alerts.alerts)  # the watchdog loop ran
    await service.stop()  # stopping twice is harmless
