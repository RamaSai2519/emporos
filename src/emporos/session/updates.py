"""Broker order updates on their way to the worker: queued by the callback, handled by the loop.

`Broker.on_order_update` calls its handler synchronously, possibly from a socket task. The handler
must not await or touch the journal, so it only queues; the worker then drains the queue in its own
ordered loop, where applying a fill and telling a strategy happen one at a time.
"""

from __future__ import annotations

from collections import deque

from emporos.broker.models import BrokerOrderUpdate


class OrderUpdateRouter:
    def __init__(self, max_queued: int = 10_000) -> None:
        self._queue: deque[BrokerOrderUpdate] = deque(maxlen=max_queued)
        self._dirty = False

    def on_update(self, update: BrokerOrderUpdate) -> None:
        self._queue.append(update)
        self._dirty = True

    def take_dirty(self) -> bool:
        """True once per burst of updates: the cue to re-read the trade book."""
        dirty, self._dirty = self._dirty, False
        return dirty

    def drain(self) -> list[BrokerOrderUpdate]:
        updates = list(self._queue)
        self._queue.clear()
        return updates
