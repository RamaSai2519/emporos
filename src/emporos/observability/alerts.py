"""Alerts that outlive a log line: persisted as system events, drained by the worker's poll loop.

`AlertSink.raise_alert` is called from synchronous code, so it cannot await a database write. It
queues a `SystemEventRecord` in an outbox and logs immediately; `EventOutbox.drain` writes the queue
to `system_events` (retaining anything it could not write), so an alert is durable as soon as the
database is reachable and never lost because it was raised in the wrong kind of function.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Protocol

from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.persistence.records import SystemEventRecord
from emporos.session.lifecycle import StateChange

_LOG = logging.getLogger("emporos.alerts")


class SystemEventStore(Protocol):
    async def insert(self, record: SystemEventRecord) -> None: ...


class EventOutbox:
    def __init__(self, max_queued: int = 10_000) -> None:
        self._queue: deque[SystemEventRecord] = deque(maxlen=max_queued)

    @property
    def pending(self) -> int:
        return len(self._queue)

    def push(self, record: SystemEventRecord) -> None:
        self._queue.append(record)

    async def drain(self, store: SystemEventStore) -> int:
        """Write queued events oldest first; an event stays queued until it is durable."""
        written = 0
        while self._queue:
            await store.insert(self._queue[0])
            self._queue.popleft()
            written += 1
        return written


class OutboxAlertSink:
    """An `AlertSink` that logs at ERROR and queues a durable `alert` system event."""

    def __init__(self, outbox: EventOutbox, clock: Clock, ids: IdGenerator) -> None:
        self._outbox = outbox
        self._clock = clock
        self._ids = ids

    def raise_alert(self, name: str, message: str) -> None:
        _LOG.error("ALERT %s: %s", name, message)
        self._outbox.push(
            SystemEventRecord.model_validate(
                {
                    "_id": self._ids.new_ulid(),
                    "type": "alert",
                    "ts": self._clock.now(),
                    "name": name,
                    "message": message,
                }
            )
        )


class LifecycleEvents:
    """Records every session state change as a `session_state` system event, tagged with the
    account whose session it is — the dashboard and any other reader must be able to tell one
    worker's session apart from another's in the shared `system_events` collection."""

    def __init__(self, outbox: EventOutbox, ids: IdGenerator, account_id: str) -> None:
        self._outbox = outbox
        self._ids = ids
        self._account_id = account_id

    def __call__(self, change: StateChange) -> None:
        self._outbox.push(
            SystemEventRecord.model_validate(
                {
                    "_id": self._ids.new_ulid(),
                    "type": "session_state",
                    "account_id": self._account_id,
                    "ts": change.at,
                    "from": change.previous.value,
                    "to": change.current.value,
                    "reason": change.reason,
                }
            )
        )
