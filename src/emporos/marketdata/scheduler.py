"""The wall-clock heartbeat that closes bars and persists them (EM-52).

Once a minute — at the boundary plus the aggregator's grace — it asks the aggregator to close
every due bar (so a silent instrument's bar closes on schedule with no tick to trigger it) and
flushes the resulting candles. It uses the injected `Clock` and `Sleeper`, never real time.
"""

from __future__ import annotations

from datetime import timedelta

from emporos.core.clock import Clock, Sleeper
from emporos.marketdata.aggregator import MinuteCandleAggregator, floor_minute
from emporos.marketdata.candle_writer import CandlePersister

_MINUTE = timedelta(minutes=1)


class MinuteScheduler:
    def __init__(
        self,
        aggregator: MinuteCandleAggregator,
        persister: CandlePersister,
        clock: Clock,
        sleeper: Sleeper,
        grace: timedelta,
    ) -> None:
        self._aggregator = aggregator
        self._persister = persister
        self._clock = clock
        self._sleeper = sleeper
        self._grace = grace

    async def run_once(self) -> None:
        """Sleep until the next boundary (+ grace), then close due bars and flush."""
        now = self._clock.now()
        target = floor_minute(now) + _MINUTE + self._grace
        await self._sleeper.sleep(max((target - now).total_seconds(), 0.0))
        self._aggregator.advance()
        await self._persister.flush()

    async def run(self) -> None:
        while True:
            await self.run_once()
