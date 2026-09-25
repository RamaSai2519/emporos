"""Record L1 quotes of near-the-money options beside the D1 stock quotes (EM-246, plan §7a).

Same rules as the stock recorder, and the stock quotes always come first:

* it is called only AFTER the stock recorder's poll and only while that recorder is not backed off,
  so a rate-limit reply on the stock quotes silences the options in the same breath;
* it has its own back-off. On a rate-limit reply it polls LESS OFTEN (the interval doubles, up to
  `max_interval`) and comes back to its normal pace after `recover_after` clean polls;
* the strike set is rebuilt from a fresh spot every `rebuild_every` (15 minutes), from the public
  scrip master loaded once a day; a failed load or spot keeps the previous set and is retried;
* it never raises for a broker problem, places nothing, and yields to an order call in flight.

Every spot and quote call goes through the same `QuoteSource` (quotes only)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from emporos.broker.models import Quote
from emporos.core.clock import Clock
from emporos.quotes.contract import ContractBook, OptionContract
from emporos.quotes.fetcher import BatchFetcher, FetchCounters, FetchSettings, QuoteSource
from emporos.quotes.option_row import OptionQuoteRow, OptionQuoteSink
from emporos.quotes.priority import NoPendingOrders, OrderPriority
from emporos.quotes.row import QuoteRow
from emporos.quotes.strikes import StrikePlanner, UnderlyingRule
from emporos.quotes.window import RecordingWindow

__all__ = ["ContractSource", "OptionCounters", "OptionQuoteRecorder", "OptionSettings"]

_LOG = logging.getLogger(__name__)


class ContractSource(Protocol):
    async def load(self) -> ContractBook:
        """The day's option contracts (the public scrip master)."""
        ...


@dataclass(frozen=True)
class OptionSettings:
    interval: timedelta = timedelta(seconds=60)
    max_interval: timedelta = timedelta(minutes=5)
    recover_after: int = 30  # clean polls before a degraded interval halves
    rebuild_every: timedelta = timedelta(minutes=15)
    retry_load_after: timedelta = timedelta(minutes=10)
    flush_every_polls: int = 10
    fetch: FetchSettings = field(default_factory=FetchSettings)

    def __post_init__(self) -> None:
        if self.interval <= timedelta(0) or self.max_interval < self.interval:
            raise ValueError("the option interval must be positive and not above its maximum")
        if self.recover_after < 1 or self.flush_every_polls < 1:
            raise ValueError("recover and flush after at least one poll")
        if self.rebuild_every <= timedelta(0):
            raise ValueError("rebuild the strike set at a positive interval")


@dataclass
class OptionCounters(FetchCounters):
    polls: int = 0
    rows: int = 0
    flushes: int = 0
    rebuilds: int = 0
    contracts: int = 0  # in the current set
    degraded: int = 0  # times the interval was lengthened
    master_failures: int = 0
    spot_failures: int = 0
    unknown_quotes: int = 0  # answers for an id not in the set


class OptionQuoteRecorder:
    def __init__(
        self,
        source: QuoteSource,
        sink: OptionQuoteSink,
        contracts: ContractSource,
        rules: Sequence[UnderlyingRule],
        settings: OptionSettings,
        clock: Clock,
        window: RecordingWindow | None = None,
        priority: OrderPriority | None = None,
    ) -> None:
        if not rules:
            raise ValueError("nothing to record")
        self._sink, self._contracts, self._rules = sink, contracts, tuple(rules)
        self._settings, self._clock = settings, clock
        self._window = window or RecordingWindow()
        self.counters = OptionCounters()
        self._fetcher = BatchFetcher(
            source, settings.fetch, clock, priority or NoPendingOrders(), self.counters
        )
        self._interval = settings.interval
        self._book: ContractBook | None = None
        self._book_retry_at: datetime | None = None
        self._set: dict[str, tuple[OptionContract, Decimal]] = {}
        self._rebuild_at: datetime | None = None
        self._next_poll: datetime | None = None
        self._clean = 0
        self._unflushed = 0

    @property
    def interval(self) -> timedelta:
        return self._interval

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(self._set)

    async def poll(self) -> None:
        now = self._clock.now()
        if not self._window.contains(now):
            self._flush()
            return
        if (self._next_poll is not None and now < self._next_poll) or self._fetcher.backed_off(now):
            return
        self.counters.polls += 1
        before = self.counters.rate_limited
        await self._refresh(now)
        fetched = await self._fetcher.fetch(list(self._set), now) if self._set else []
        rows = self._rows(fetched)
        if rows:
            self._sink.append(rows)
            self.counters.rows += len(rows)
        self._pace(self.counters.rate_limited > before)
        self._next_poll = now + self._interval
        self._unflushed += 1
        if self._unflushed >= self._settings.flush_every_polls:
            self._flush()

    def close(self) -> int:
        return self._flush()

    # --- the strike set ------------------------------------------------------------------
    async def _refresh(self, now: datetime) -> None:
        if self._rebuild_at is not None and now < self._rebuild_at:
            return
        book = await self._book_today(now)
        if book is None:
            return
        spots = await self._spots(now)
        if not spots:
            self.counters.spot_failures += 1
            self._rebuild_at = now + min(self._settings.rebuild_every, self._interval)
            return
        planner = StrikePlanner(book)
        chosen: dict[str, tuple[OptionContract, Decimal]] = {}
        for rule in self._rules:
            spot = spots.get(rule.spot_id)
            if spot is None:
                continue
            for contract in planner.plan(rule, spot, now.date()):
                chosen[contract.instrument_id] = (contract, spot)
        if chosen:
            self._set = chosen
            self.counters.contracts = len(chosen)
        self.counters.rebuilds += 1
        self._rebuild_at = now + self._settings.rebuild_every

    async def _book_today(self, now: datetime) -> ContractBook | None:
        if self._book is not None:
            return self._book
        if self._book_retry_at is not None and now < self._book_retry_at:
            return None
        try:
            self._book = await self._contracts.load()
        except Exception as error:  # a scrip master that will not load must not stop the day
            self.counters.master_failures += 1
            self._book_retry_at = now + self._settings.retry_load_after
            _LOG.warning("option recorder: scrip master not loaded: %s", error)
        return self._book

    async def _spots(self, now: datetime) -> dict[str, Decimal]:
        ids = list(dict.fromkeys(rule.spot_id for rule in self._rules))
        got = await self._fetcher.fetch(ids, now)
        return {quote.instrument_id: quote.ltp.amount for quote, _ in got}

    # --- rows and pace -------------------------------------------------------------------
    def _rows(self, fetched: Sequence[tuple[Quote, datetime]]) -> list[OptionQuoteRow]:
        rows: list[OptionQuoteRow] = []
        for quote, received in fetched:
            held = self._set.get(quote.instrument_id)
            if held is None:
                self.counters.unknown_quotes += 1
                continue
            contract, spot = held
            rows.append(
                OptionQuoteRow(
                    QuoteRow.from_quote(quote, received), contract, spot, quote.open_interest
                )
            )
        return rows

    def _pace(self, rate_limited: bool) -> None:
        s = self._settings
        if rate_limited:
            self._clean = 0
            longer = min(s.max_interval, self._interval * 2)
            if longer > self._interval:
                self.counters.degraded += 1
                _LOG.warning("option recorder: polling every %s from now on", longer)
            self._interval = longer
            return
        self._clean += 1
        if self._interval > s.interval and self._clean >= s.recover_after:
            self._interval = max(s.interval, self._interval / 2)
            self._clean = 0

    def _flush(self) -> int:
        self._unflushed = 0
        written = self._sink.flush()
        if written:
            self.counters.flushes += 1
        return written
