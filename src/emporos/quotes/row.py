"""One recorded L1 quote."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.broker.models import Quote

__all__ = ["QuoteRow"]


def _utc(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class QuoteRow:
    instrument_id: str  # "NSE:2885"
    received_at: datetime  # when we got the reply (UTC)
    exchange_ts: datetime | None  # the exchange's own time of the last trade, when it sent one
    ltp: Decimal
    bid: Decimal | None  # None: that side of the book was empty
    ask: Decimal | None
    bid_qty: int | None
    ask_qty: int | None
    volume: int | None

    def __post_init__(self) -> None:
        _utc("received_at", self.received_at)
        _utc("exchange_ts", self.exchange_ts)

    @classmethod
    def from_quote(cls, quote: Quote, received_at: datetime) -> QuoteRow:
        return cls(
            quote.instrument_id,
            received_at,
            quote.exchange_ts,
            quote.ltp.amount,
            quote.bid.amount if quote.bid is not None else None,
            quote.ask.amount if quote.ask is not None else None,
            quote.bid_qty,
            quote.ask_qty,
            quote.volume,
        )
