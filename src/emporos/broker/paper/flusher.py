"""Keeps the write-behind journal durable while ticks fill orders.

Fills happen inside a synchronous tick handler and can only be queued; this task writes the queue
out on a cadence, and once more when it is cancelled at shutdown. A failed flush raises an alert
and is retried on the next interval — the entries stay queued and every write is idempotent.
"""

from __future__ import annotations

from emporos.broker.paper.journal import PaperJournal
from emporos.core.alerts import AlertSink
from emporos.core.clock import Sleeper


class JournalFlusher:
    def __init__(
        self, journal: PaperJournal, sleeper: Sleeper, alerts: AlertSink, interval_seconds: float
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("the flush interval must be positive")
        self._journal = journal
        self._sleeper = sleeper
        self._alerts = alerts
        self._interval = interval_seconds

    async def run(self) -> None:
        try:
            while True:
                await self._sleeper.sleep(self._interval)
                await self.flush_once()
        finally:
            await self.flush_once()  # shutdown: leave nothing queued if the store will take it

    async def flush_once(self) -> None:
        try:
            await self._journal.flush()
        except Exception as error:
            self._alerts.raise_alert("paper_journal_flush_failed", repr(error))
