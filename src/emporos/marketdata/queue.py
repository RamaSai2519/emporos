"""The bounded tick queue between the socket reader and the pipeline (plan.md §7).

The reader must never block on a slow consumer, and an unbounded queue turns a stall into an
out-of-memory crash. So the queue is bounded (~10k) and, when full, **drops the oldest** tick —
fresh prices matter more than stale ones — and raises an alarm so the loss is never silent.
Each tick is stamped with its arrival time on entry, not when it is eventually dequeued.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.core.errors import ConfigurationError
from emporos.domain.ticks import RawTick

_LOG = logging.getLogger(__name__)
DEFAULT_MAX_SIZE = 10_000


@dataclass(frozen=True)
class QueuedTick:
    tick: RawTick
    received_ts: datetime


class BoundedTickQueue:
    """Satisfies the reader's `TickSink`: `on_raw_tick` never blocks and never raises."""

    def __init__(
        self, clock: Clock, alerts: AlertSink | None = None, max_size: int = DEFAULT_MAX_SIZE
    ) -> None:
        if max_size < 1:
            raise ConfigurationError("the tick queue needs room for at least one tick")
        self._clock = clock
        self._alerts = alerts
        self._queue: asyncio.Queue[QueuedTick] = asyncio.Queue(maxsize=max_size)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    def __len__(self) -> int:
        return self._queue.qsize()

    def on_raw_tick(self, tick: RawTick) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # drop the oldest
            self._dropped += 1
            self._alarm()
        self._queue.put_nowait(QueuedTick(tick, self._clock.now()))

    async def get(self) -> QueuedTick:
        return await self._queue.get()

    def get_nowait(self) -> QueuedTick:
        return self._queue.get_nowait()

    def _alarm(self) -> None:
        count = self._dropped
        is_power_of_ten = str(count)[0] == "1" and set(str(count)[1:]) <= {"0"}
        if is_power_of_ten:  # 1, 10, 100, ... — audible without flooding the channel
            message = f"tick queue overflowed; {count} oldest tick(s) dropped so far"
            _LOG.error(message)
            if self._alerts is not None:
                self._alerts.raise_alert("market_data.queue_overflow", message)
