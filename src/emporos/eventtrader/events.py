"""What the event trader reads: point-in-time events and the as-of market context (EM-240).

These are the Protocols the data side implements (the filings store, headlines, the context
builder) and the pipeline and the replay depend on. They hold no I/O."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = ["ContextBuilder", "EventStore", "MarketContext", "MarketEvent"]

FILING = "filing"
HEADLINE = "headline"


@dataclass(frozen=True)
class MarketEvent:
    """One filing or headline, exactly as it became public.

    `published_at` is the source's own timestamp (for a filing, the exchange's dissemination time).
    `usable_from` is the first instant a decision may use it: the same as `published_at` for a
    timestamped item, the NEXT session's 09:15 for a headline that carries only a date. The store
    computes it, so no consumer guesses."""

    event_id: str
    instrument_id: str  # "NSE:<token>"; "" for a market-wide item or a name with no token
    symbol: str  # the trading symbol, shown to the model (this track allows names, §12.1)
    published_at: datetime
    usable_from: datetime
    kind: str  # FILING or HEADLINE
    category: str
    subject: str
    text: str  # the filing's own text or the headline; may be long, callers truncate

    def __post_init__(self) -> None:
        if not self.event_id or not self.symbol:
            raise ValueError("an event needs an id and a symbol")
        if self.published_at.tzinfo is None or self.usable_from.tzinfo is None:
            raise ValueError("event times must be timezone-aware")
        if self.usable_from < self.published_at:
            raise ValueError("an event cannot be usable before it is published")
        if self.kind not in (FILING, HEADLINE):
            raise ValueError(f"unknown event kind {self.kind!r}")


class EventStore(Protocol):
    def events_between(self, start: datetime, end: datetime) -> Sequence[MarketEvent]:
        """Events with `published_at` in [start, end), sorted by (published_at, event_id),
        deduplicated across NSE and BSE."""
        ...

    def for_symbol(self, symbol: str, start: datetime, end: datetime) -> Sequence[MarketEvent]:
        """The same, for one trading symbol."""
        ...

    def by_id(self, event_id: str) -> MarketEvent | None: ...


@dataclass(frozen=True)
class MarketContext:
    """As-of numbers about the market and the name, keyed by stable names (the name's move since
    the previous close and since the event, VWAP distance, volume against its time-of-day norm,
    5 and 20 day returns and volatility, NIFTY, sector and INDIA VIX moves, open interest where
    known). A value that cannot be known at the decision time is left out, never filled in."""

    lines: Mapping[str, str | float]


class ContextBuilder(Protocol):
    def context(self, event: MarketEvent, decision_at: datetime) -> MarketContext:
        """May read only bars whose close is at or before `decision_at` and sessions before it."""
        ...
