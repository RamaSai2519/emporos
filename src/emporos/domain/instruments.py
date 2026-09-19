"""Tradable instruments and how strategies look them up.

Strategies reference instruments by `(exchange, tradingsymbol)` and resolve them
through an `InstrumentResolver` — no hardcoded tokens anywhere (plan.md §8). The
Protocol lives here so strategies can depend on it without importing storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from emporos.domain.money import Money


class Exchange(StrEnum):
    NSE = "NSE"
    BSE = "BSE"


@dataclass(frozen=True)
class Instrument:
    """One NSE/BSE cash instrument as defined by the exchange master."""

    exchange: Exchange
    token: str
    tradingsymbol: str
    name: str
    lot_size: int
    tick_size: Money

    def __post_init__(self) -> None:
        if not self.token or not self.tradingsymbol:
            raise ValueError("an instrument needs a token and a tradingsymbol")
        if self.lot_size <= 0:
            raise ValueError("lot size must be positive")
        if self.tick_size <= Money.zero():
            raise ValueError("tick size must be positive")

    @property
    def instrument_id(self) -> str:
        """Stable across syncs: orders and candles reference this, never a mutable field."""
        return f"{self.exchange.value}:{self.token}"


class UnknownInstrumentError(LookupError):
    """No instrument matches the lookup."""


class InstrumentResolver(Protocol):
    def by_token(self, exchange: Exchange, token: str) -> Instrument: ...

    def by_symbol(self, exchange: Exchange, tradingsymbol: str) -> Instrument: ...

    def by_id(self, instrument_id: str) -> Instrument: ...
