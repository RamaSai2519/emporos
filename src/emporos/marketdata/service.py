"""Runs the market-data loops together: tick pipeline, minute scheduler, staleness watchdog.

The loops run as tasks and `stop()` cancels them all. The feed client's own `run()` is started by
the composition root beside this service, because it owns the socket lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib

from emporos.core.clock import Sleeper
from emporos.marketdata.pipeline import TickPipeline
from emporos.marketdata.scheduler import MinuteScheduler
from emporos.marketdata.staleness import StalenessWatchdog

WATCHDOG_INTERVAL_SECONDS = 1.0


class MarketDataService:
    def __init__(
        self,
        pipeline: TickPipeline,
        scheduler: MinuteScheduler,
        watchdog: StalenessWatchdog,
        sleeper: Sleeper,
        watchdog_interval: float = WATCHDOG_INTERVAL_SECONDS,
    ) -> None:
        self._pipeline = pipeline
        self._scheduler = scheduler
        self._watchdog = watchdog
        self._sleeper = sleeper
        self._watchdog_interval = watchdog_interval
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def running(self) -> bool:
        return bool(self._tasks)

    def start(self) -> None:
        if self._tasks:
            return
        loops = (self._pipeline.run(), self._scheduler.run(), self._watch())
        self._tasks = [asyncio.create_task(loop) for loop in loops]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _watch(self) -> None:
        while True:
            await self._sleeper.sleep(self._watchdog_interval)
            self._watchdog.check()
