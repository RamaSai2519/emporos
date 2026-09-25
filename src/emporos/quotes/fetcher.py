"""Fetch quotes in batches without ever hurting the rest of the process (EM-217, EM-246).

One `BatchFetcher` is one stream of quote calls with its OWN back-off and counters: a rate-limit
reply (HTTP 403 plain text or 429) stops it, doubling the wait from `backoff_initial` up to
`backoff_max`; an order call in flight makes it stand aside; a poll is bounded by `budget`; a broker
error is counted, never raised. The D1 recorder and the option recorder each hold one, so the
options can be degraded or backed off while the D1 stock quotes carry on."""

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
from emporos.quotes.priority import OrderPriority

__all__ = ["BatchFetcher", "FetchSettings", "FetchCounters", "QuoteSource"]

_LOG = logging.getLogger(__name__)
MAX_BATCH = 50  # Angel One's market-data quote call takes at most 50 symbols


class QuoteSource(Protocol):
    """The one call a recorder makes."""

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]: ...


@dataclass(frozen=True)
class FetchSettings:
    batch_size: int = MAX_BATCH
    request_timeout: timedelta = timedelta(seconds=8)
    budget: timedelta = timedelta(seconds=20)  # the whole poll, all batches
    backoff_initial: timedelta = timedelta(seconds=60)
    backoff_max: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= MAX_BATCH:
            raise ValueError(f"the batch size must be 1 to {MAX_BATCH}")
        if self.request_timeout <= timedelta(0):
            raise ValueError("the interval and the request timeout must be positive")
        if self.backoff_initial <= timedelta(0) or self.backoff_max < self.backoff_initial:
            raise ValueError("the backoff must start positive and not shrink")


@dataclass
class FetchCounters:
    stale_dropped: int = 0
    no_exchange_time: int = 0
    orders_yielded: int = 0
    backed_off: int = 0
    rate_limited: int = 0
    errors: int = 0
    timeouts: int = 0
    over_budget: int = 0
    by_error: dict[str, int] = field(default_factory=dict)


class BatchFetcher:
    def __init__(
        self,
        source: QuoteSource,
        settings: FetchSettings,
        clock: Clock,
        priority: OrderPriority,
        counters: FetchCounters | None = None,
    ) -> None:
        self._source, self._settings, self._clock = source, settings, clock
        self._priority = priority
        self.counters = counters or FetchCounters()
        self._resume_at: datetime | None = None
        self._streak = 0

    def backed_off(self, now: datetime) -> bool:
        """True (and counted) while a rate-limit wait is still running."""
        if self._resume_at is not None and now < self._resume_at:
            self.counters.backed_off += 1
            return True
        return False

    def is_backed_off(self, now: datetime) -> bool:
        """The same question, without counting it."""
        return self._resume_at is not None and now < self._resume_at

    async def fetch(self, ids: Sequence[str], started: datetime) -> list[tuple[Quote, datetime]]:
        """Fresh quotes with the instant each reply arrived (stale ones are dropped and counted)."""
        s, out = self._settings, []
        deadline = started + s.budget
        for start in range(0, len(ids), s.batch_size):
            batch = list(ids[start : start + s.batch_size])
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
            out.extend(self._fresh(quotes))
        return out

    def _fresh(self, quotes: Sequence[Quote]) -> list[tuple[Quote, datetime]]:
        received = self._clock.now()
        today = received.astimezone(IST).date()
        kept: list[tuple[Quote, datetime]] = []
        for quote in quotes:
            if quote.exchange_ts is None:
                self.counters.no_exchange_time += 1  # cannot be checked for staleness: kept
            elif quote.exchange_ts.astimezone(IST).date() != today:
                self.counters.stale_dropped += 1  # an exchange holiday, or a name not trading
                continue
            kept.append((quote, received))
        return kept

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
