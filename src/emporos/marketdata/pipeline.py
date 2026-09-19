"""Queue -> normalizer -> subscribers (plan.md §7).

Dispatch is synchronous and totally ordered: one consumer, and every subscriber sees a tick
before the next is normalized, so a fill applies before the next signal evaluates. A subscriber
that raises is isolated — counted, alerted, and skipped for that tick — because one broken
strategy must never take the session down.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.domain.ticks import Tick
from emporos.marketdata.normalizer import TickNormalizer
from emporos.marketdata.queue import BoundedTickQueue, QueuedTick

_LOG = logging.getLogger(__name__)


class TickSubscriber(Protocol):
    def on_tick(self, tick: Tick) -> None: ...


class TickPipeline:
    def __init__(
        self,
        queue: BoundedTickQueue,
        normalizer: TickNormalizer,
        subscribers: Sequence[TickSubscriber],
        alerts: AlertSink | None = None,
    ) -> None:
        self._queue = queue
        self._normalizer = normalizer
        self._subscribers = tuple(subscribers)
        self._alerts = alerts
        self._subscriber_errors = 0

    @property
    def subscriber_errors(self) -> int:
        return self._subscriber_errors

    async def run(self) -> None:
        """Consume forever (cancel to stop)."""
        while True:
            self.process(await self._queue.get())

    def drain(self) -> int:
        """Process everything currently queued; returns how many ticks were consumed."""
        consumed = 0
        while len(self._queue):
            self.process(self._queue.get_nowait())
            consumed += 1
        return consumed

    def process(self, queued: QueuedTick) -> None:
        tick = self._normalizer.normalize(queued)
        if tick is None:
            return
        for subscriber in self._subscribers:
            try:
                subscriber.on_tick(tick)
            except Exception:
                self._subscriber_errors += 1
                _LOG.exception("tick subscriber %s failed; isolated", type(subscriber).__name__)
                if self._alerts is not None:
                    self._alerts.raise_alert(
                        "market_data.subscriber_failed",
                        f"{type(subscriber).__name__} raised while handling {tick.instrument_id}",
                    )
