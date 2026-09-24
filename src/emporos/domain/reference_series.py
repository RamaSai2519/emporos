"""Non-tradable reference series: market indices and volatility (EM-191 D2, plan §4.2).

Regime conditioning, beta hedging and lead-lag need the market's own series (NIFTY 50, sector
indices, INDIA VIX). They are candles like any other, but they are NOT instruments: nothing can be
bought or sold in them, and the instrument master leaves them out on purpose (`CashSegmentFilter`
keeps only cash equity rows). A `ReferenceSeries` is the small, separate thing that names one, so a
strategy can never resolve an index into something it could place an order on.

Its candles are stored under `series_id` (`NSE:<token>`) through the ordinary `CandleRepository`.
Index tokens (99926000 and up) do not collide with cash tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money

__all__ = ["ReferenceSeries", "SeriesKind"]

_INDEX_TICK = Money.of("0.01")  # indices have no tick; candles need a positive one to be handled


class SeriesKind(StrEnum):
    INDEX = "index"
    VOLATILITY = "volatility"


@dataclass(frozen=True)
class ReferenceSeries:
    token: str  # the exchange's index token, from the scrip master
    symbol: str  # the scrip master's `symbol`, e.g. "Nifty 50"
    name: str  # the scrip master's `name`, e.g. "NIFTY"
    kind: SeriesKind

    def __post_init__(self) -> None:
        if not (self.token.strip() and self.symbol.strip() and self.name.strip()):
            raise ValueError("a reference series needs a token, a symbol and a name")

    @property
    def series_id(self) -> str:
        return f"{Exchange.NSE.value}:{self.token}"

    def fetch_handle(self) -> Instrument:
        """What the bar-fetch pipeline needs to ask the broker for this series' candles. It is used
        only to fetch: it is never registered in the instrument master, so it cannot be resolved,
        sized or ordered."""
        return Instrument(Exchange.NSE, self.token, self.symbol, self.name, 1, _INDEX_TICK)
