"""Poll L1 quotes for a fixed set of instruments and hand them to a sink (EM-217).

One `poll()` is one scheduler job. It never places or cancels an order and never raises for a broker
problem: a rate-limit reply (HTTP 403 plain text or 429) backs the recorder off, doubling from
`backoff_initial` up to `backoff_max`, and a poll that meets an order call in flight gives way to
it. A poll is bounded by `budget`, so a slow broker cannot hold up the jobs sharing its loop."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from emporos.core.clock import Clock
from emporos.quotes.fetcher import (
    MAX_BATCH,
    BatchFetcher,
    FetchCounters,
    FetchSettings,
    QuoteSource,
)
from emporos.quotes.priority import NoPendingOrders, OrderPriority
from emporos.quotes.row import QuoteRow
from emporos.quotes.sink import QuoteSink
from emporos.quotes.window import RecordingWindow

__all__ = [
    "MAX_BATCH", "QuoteRecorder", "QuoteSource", "RecorderCounters", "RecorderSettings",
]  # fmt: skip

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecorderSettings:
    interval: timedelta = timedelta(seconds=60)  # how often the job runs
    batch_size: int = MAX_BATCH
    request_timeout: timedelta = timedelta(seconds=8)
    budget: timedelta = timedelta(seconds=20)  # the whole poll, all batches
    backoff_initial: timedelta = timedelta(seconds=60)
    backoff_max: timedelta = timedelta(minutes=15)
    flush_every_polls: int = 10

    def __post_init__(self) -> None:
        self.fetch()  # the fetch fields validate themselves
        if self.interval <= timedelta(0):
            raise ValueError("the interval and the request timeout must be positive")
        if self.flush_every_polls < 1:
            raise ValueError("flush at least every poll")

    def fetch(self) -> FetchSettings:
        return FetchSettings(
            self.batch_size, self.request_timeout, self.budget, self.backoff_initial,
            self.backoff_max,
        )  # fmt: skip


@dataclass
class RecorderCounters(FetchCounters):
    polls: int = 0
    rows: int = 0
    flushes: int = 0


class QuoteRecorder:
    def __init__(
        self,
        source: QuoteSource,
        sink: QuoteSink,
        instrument_ids: Sequence[str],
        settings: RecorderSettings,
        clock: Clock,
        window: RecordingWindow | None = None,
        priority: OrderPriority | None = None,
    ) -> None:
        ids = list(dict.fromkeys(instrument_ids))
        if not ids:
            raise ValueError("nothing to record")
        self._sink, self._ids = sink, ids
        self._settings, self._clock = settings, clock
        self._window = window or RecordingWindow()
        self._priority = priority or NoPendingOrders()
        self.counters = RecorderCounters()
        self._fetcher = BatchFetcher(source, settings.fetch(), clock, self._priority, self.counters)
        self._unflushed_polls = 0

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(self._ids)

    def is_backed_off(self) -> bool:
        """True while a rate-limit wait is running: whatever shares the API stands aside."""
        return self._fetcher.is_backed_off(self._clock.now())

    async def poll(self) -> None:
        now = self._clock.now()
        if not self._window.contains(now):
            if self._flush():  # the session just ended: nothing stays in memory overnight
                _LOG.info("quote recorder day summary: %s", self.counters)
            return
        if self._fetcher.backed_off(now):
            return
        if self._priority.orders_pending():
            self.counters.orders_yielded += 1
            return
        self.counters.polls += 1
        fetched = await self._fetcher.fetch(self._ids, now)
        rows = [QuoteRow.from_quote(quote, received) for quote, received in fetched]
        if rows:
            self._sink.append(rows)
            self.counters.rows += len(rows)
        self._unflushed_polls += 1
        if self._unflushed_polls >= self._settings.flush_every_polls:
            self._flush()

    def close(self) -> int:
        """Write what is still in memory (end of the process)."""
        return self._flush()

    def _flush(self) -> int:
        self._unflushed_polls = 0
        written = self._sink.flush()
        if written:
            self.counters.flushes += 1
        return written
