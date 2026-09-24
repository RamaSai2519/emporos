"""What a screened signal produces, and how many shares it trades (EM-191 F3, plan §4.1).

A signal scan owns its entry and exit rule and hands back `ScreenTrade`s at the prices it assumes
it fills at, BEFORE any slippage: the screener charges slippage and fees itself, from the
benchmark's cost scenarios, so a scan can never flatter itself by baking a friendlier fill in.
`DeclaredValueSizer` turns the declared position value into whole shares, the way the risk engine
does (round down; a name too dear for one share of the declared value is skipped, not rounded up).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.sizing import DeclaredSize

__all__ = ["DeclaredValueSizer", "PositionSizer", "ScreenTrade"]


@dataclass(frozen=True)
class ScreenTrade:
    """One round trip, entered and closed the same session, at the scan's own fill prices."""

    instrument_id: str  # "NSE:2885"
    day: date  # the IST session it traded
    side: OrderSide  # BUY = long, SELL = short
    entry_price: Money
    exit_price: Money

    def __post_init__(self) -> None:
        if self.entry_price.amount <= 0 or self.exit_price.amount <= 0:
            raise ValueError("a trade needs positive prices")

    @property
    def exchange(self) -> Exchange:
        return Exchange(self.instrument_id.split(":", 1)[0])

    @property
    def gross_return(self) -> Decimal:
        """The move captured, as a fraction of the entry price (positive is a profit)."""
        move = (self.exit_price.amount - self.entry_price.amount) / self.entry_price.amount
        return move if self.side is OrderSide.BUY else -move


class PositionSizer(Protocol):
    def quantity(self, price: Money) -> int:
        """Whole shares to trade at `price`; 0 means the trade cannot be taken."""
        ...


class DeclaredValueSizer:
    def __init__(self, position_value: Decimal) -> None:
        self._size = DeclaredSize(position_value)

    @property
    def position_value(self) -> Decimal:
        return self._size.position_value

    def quantity(self, price: Money) -> int:
        return self._size.quantity_at(price)
