"""Market ticks (plan.md §7). Prices are `Money`; every timestamp is timezone-aware UTC.

`RawTick` is what a feed reports about a token: no instrument resolution, no session or duplicate
filtering yet. `Tick` is the normalized form the rest of the platform consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from emporos.domain.instruments import Exchange
from emporos.domain.money import Money


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True)
class QuoteData:
    """The extra fields a QUOTE-mode frame carries."""

    average_price: Money
    total_buy_quantity: Decimal
    total_sell_quantity: Decimal
    day_open: Money
    day_high: Money
    day_low: Money
    previous_close: Money


@dataclass(frozen=True)
class RawTick:
    exchange: Exchange
    token: str
    exchange_ts: datetime
    ltp: Money
    sequence: int
    # Cumulative shares traded today. Only QUOTE-mode frames carry it; LTP-mode frames do not.
    volume: int | None = None
    last_traded_quantity: int | None = None
    quote: QuoteData | None = None

    def __post_init__(self) -> None:
        _require_utc("exchange_ts", self.exchange_ts)
        if not self.token:
            raise ValueError("a tick needs a token")
        if self.ltp <= Money.zero():
            raise ValueError("a tick's price must be positive")
        if self.volume is not None and self.volume < 0:
            raise ValueError("volume cannot be negative")


@dataclass(frozen=True)
class Tick:
    """A normalized tick. `out_of_order` ticks are usable for the trade record but must never
    be folded into a candle that has already closed."""

    instrument_id: str
    exchange_ts: datetime
    received_ts: datetime
    ltp: Money
    sequence: int
    volume: int | None = None
    out_of_order: bool = False

    def __post_init__(self) -> None:
        _require_utc("exchange_ts", self.exchange_ts)
        _require_utc("received_ts", self.received_ts)
        if self.ltp <= Money.zero():
            raise ValueError("a tick's price must be positive")
