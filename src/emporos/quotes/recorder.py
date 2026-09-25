"""Poll L1 quotes for a fixed set of instruments and hand them to a sink (EM-217).

One `poll()` is one scheduler job. It never places or cancels an order and never raises for a broker
problem: a rate-limit reply (HTTP 403 plain text or 429) backs the recorder off, doubling from
`backoff_initial` up to `backoff_max`, and a poll that meets an order call in flight gives way to
it. A poll is bounded by `budget`, so a slow broker cannot hold up the jobs sharing its loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from emporos.broker.errors import BrokerError, BrokerRateLimitedError
from emporos.broker.models import Quote
from emporos.core.clock import IST, Clock
from emporos.quotes.priority import NoPendingOrders, OrderPriority
from emporos.quotes.row import QuoteRow
from emporos.quotes.sink import QuoteSink
from emporos.quotes.window import RecordingWindow

__all__ = ["QuoteRecorder", "QuoteSource", "RecorderCounters", "RecorderSettings"]

_LOG = logging.getLogger(__name__)
MAX_BATCH = 50  # Angel One's market-data quote call takes at most 50 symbols


class QuoteSource(Protocol):
    """The one call the recorder makes."""

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]: ...


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
        if not 1 <= self.batch_size <= MAX_BATCH:
            raise ValueError(f"the batch size must be 1 to {MAX_BATCH}")
        if self.interval <= timedelta(0) or self.request_timeout <= timedelta(0):
            raise ValueError("the interval and the request timeout must be positive")
        if self.backoff_initial <= timedelta(0) or self.backoff_max < self.backoff_initial:
            raise ValueError("the backoff must start positive and not shrink")
        if self.flush_every_polls < 1:
            raise ValueError("flush at least every poll")


@dataclass
class RecorderCounters:
    polls: int = 0
    rows: int = 0
    stale_dropped: int = 0
    no_exchange_time: int = 0
    orders_yielded: int = 0
    backed_off: int = 0
    rate_limited: int = 0
    errors: int = 0
    timeouts: int = 0
    over_budget: int = 0
    flushes: int = 0
    by_error: dict[str, int] = field(default_factory=dict)


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
        self._source, self._sink, self._ids = source, sink, ids
        self._settings, self._clock = settings, clock
        self._window = window or RecordingWindow()
        self._priority = priority or NoPendingOrders()
        self.counters = RecorderCounters()
        self._resume_at: datetime | None = None
        self._streak = 0
        self._unflushed_polls = 0

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(self._ids)

    async def poll(self) -> None:
        now = self._clock.now()
        if not self._window.contains(now):
            if self._flush():  # the session just ended: nothing stays in memory overnight
                _LOG.info("quote recorder day summary: %s", self.counters)
            return
        if self._resume_at is not None and now < self._resume_at:
            self.counters.backed_off += 1
            return
        if self._priority.orders_pending():
            self.counters.orders_yielded += 1
            return
        self.counters.polls += 1
        rows = await self._collect(now)
        if rows:
            self._sink.append(rows)
            self.counters.rows += len(rows)
        self._unflushed_polls += 1
        if self._unflushed_polls >= self._settings.flush_every_polls:
            self._flush()

    def close(self) -> int:
        """Write what is still in memory (end of the process)."""
        return self._flush()

    # --- one poll ------------------------------------------------------------------------
    async def _collect(self, started: datetime) -> list[QuoteRow]:
        s = self._settings
        rows: list[QuoteRow] = []
        deadline = started + s.budget
        for start in range(0, len(self._ids), s.batch_size):
            batch = self._ids[start : start + s.batch_size]
            if start and self._priority.orders_pending():
                self.counters.orders_yielded += 1  # an order arrived: leave the rest for later
                break
            remaining = deadline - self._clock.now()
            if remaining <= timedelta(0):
                self.counters.over_budget += 1
                break
            try:
                quotes = await asyncio.wait_for(
                    self._source.get_quote(batch),
                    min(s.request_timeout, remaining).total_seconds(),
                )
            except BrokerRateLimitedError:
                self._back_off()
                break
            except TimeoutError:
                self.counters.timeouts += 1
                continue
            except BrokerError as error:
                self._note_error(error)
                continue
            self._streak = 0
            rows.extend(self._rows(quotes))
        return rows

    def _rows(self, quotes: Sequence[Quote]) -> list[QuoteRow]:
        received = self._clock.now()
        today = received.astimezone(IST).date()
        rows: list[QuoteRow] = []
        for quote in quotes:
            if quote.exchange_ts is None:
                self.counters.no_exchange_time += 1  # cannot be checked for staleness: kept
            elif quote.exchange_ts.astimezone(IST).date() != today:
                self.counters.stale_dropped += 1  # an exchange holiday, or a name not trading
                continue
            rows.append(QuoteRow.from_quote(quote, received))
        return rows

    def _back_off(self) -> None:
        s = self._settings
        delay = min(s.backoff_max, s.backoff_initial * 2**self._streak)
        self._streak += 1
        self.counters.rate_limited += 1
        self._resume_at = self._clock.now() + delay
        _LOG.warning("quote recorder rate limited: backing off %s", delay)

    def _note_error(self, error: BrokerError) -> None:
        self.counters.errors += 1
        name = type(error).__name__
        self.counters.by_error[name] = self.counters.by_error.get(name, 0) + 1
        _LOG.warning("quote recorder: %s", error)

    def _flush(self) -> int:
        self._unflushed_polls = 0
        written = self._sink.flush()
        if written:
            self.counters.flushes += 1
        return written
