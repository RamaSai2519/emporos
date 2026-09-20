"""Closed bars on their way to the strategies: a `CandleSubscriber` that queues; the host drains."""

from __future__ import annotations

from collections import deque

from emporos.domain.candles import Candle, Timeframe
from emporos.strategies.runner import MarketEvent


class ClosedBarQueue:
    """Subscribe this to the timeframe deriver; the strategy host drains it once per poll.

    Only the timeframes strategies asked for are kept, so an unused one cannot fill the queue.
    """

    def __init__(self, timeframes: frozenset[Timeframe], max_queued: int = 100_000) -> None:
        self._timeframes = timeframes
        self._queue: deque[Candle] = deque(maxlen=max_queued)

    def on_candle(self, candle: Candle) -> None:
        if candle.timeframe in self._timeframes:
            self._queue.append(candle)

    def drain(self) -> list[MarketEvent]:
        events: list[MarketEvent] = list(self._queue)
        self._queue.clear()
        return events
