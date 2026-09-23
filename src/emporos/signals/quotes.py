"""The market a signal was decided against (EM-185), kept with the signal for later parity work.

Risk already fetches a quote to judge every signal; recording THAT quote (never a second fetch) is
what lets a paper-vs-backtest comparison measure slippage from the price the system actually saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from emporos.domain.money import Money


@dataclass(frozen=True)
class DecisionQuote:
    ts: datetime
    source: str
    ltp: Money | None = None
    bid: Money | None = None
    ask: Money | None = None

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() != UTC.utcoffset(None):
            raise ValueError("a decision quote ts must be timezone-aware UTC")
        if self.ltp is None and self.bid is None and self.ask is None:
            raise ValueError("a decision quote needs at least one price")


class DecisionQuotes(Protocol):
    def take(self, instrument_id: str) -> DecisionQuote | None:
        """The quote the last review of this instrument used, if any. Taking consumes it, so a
        review that never fetched a quote cannot be stamped with an older signal's."""
        ...


class QuoteStamper(Protocol):
    async def stamp_quote(self, signal_id: str, quote: DecisionQuote) -> None: ...
