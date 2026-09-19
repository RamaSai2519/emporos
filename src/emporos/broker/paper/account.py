"""The simulated account: positions, realised P&L, fees and cash, derived from trades alone.

Everything here is a pure function of the trades applied (and the fee charged on each), so a
restart replays the persisted executions and arrives at exactly the same account.
"""

from __future__ import annotations

from dataclasses import dataclass

from emporos.broker.models import BrokerTrade
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

_ZERO = Money.zero()


@dataclass(frozen=True)
class PositionState:
    """One instrument's position today. `net_quantity` is negative when short."""

    instrument_id: str
    net_quantity: int
    average_price: Money  # of the open quantity; zero when flat
    gross_realised: Money  # profit booked by closing quantity, before charges
    fees: Money  # charges paid on this instrument's trades

    @property
    def realised(self) -> Money:
        """What the closed part of the position actually made, after charges."""
        return self.gross_realised - self.fees


class PaperAccount:
    def __init__(self, starting_cash: Money) -> None:
        if starting_cash < _ZERO:
            raise ValueError("starting cash cannot be negative")
        self._starting_cash = starting_cash
        self._positions: dict[str, PositionState] = {}
        self._trades: list[BrokerTrade] = []

    @property
    def starting_cash(self) -> Money:
        return self._starting_cash

    @property
    def cash(self) -> Money:
        """Starting cash plus everything booked so far, after charges (unrealised excluded)."""
        return self._starting_cash + self.realised

    @property
    def realised(self) -> Money:
        total = _ZERO
        for position in self._positions.values():
            total = total + position.realised
        return total

    @property
    def fees(self) -> Money:
        total = _ZERO
        for position in self._positions.values():
            total = total + position.fees
        return total

    def trades(self) -> list[BrokerTrade]:
        return list(self._trades)

    def position(self, instrument_id: str) -> PositionState | None:
        return self._positions.get(instrument_id)

    def positions(self) -> list[PositionState]:
        return list(self._positions.values())

    def apply(self, trade: BrokerTrade, fees: Money) -> PositionState:
        """Book one trade and the charges on it; returns the instrument's position afterwards."""
        if fees < _ZERO:
            raise ValueError("charges cannot be negative")
        before = self._positions.get(trade.instrument_id) or PositionState(
            trade.instrument_id, 0, _ZERO, _ZERO, _ZERO
        )
        after = self._book(before, trade, fees)
        self._positions[trade.instrument_id] = after
        self._trades.append(trade)
        return after

    @staticmethod
    def _book(before: PositionState, trade: BrokerTrade, fees: Money) -> PositionState:
        signed = trade.quantity if trade.side is OrderSide.BUY else -trade.quantity
        net = before.net_quantity
        new_net = net + signed
        adding = net == 0 or (net > 0) == (signed > 0)
        if adding:
            held = abs(net) * before.average_price.amount
            average = Money((held + trade.quantity * trade.price.amount) / abs(new_net))
            gross = before.gross_realised
        else:
            closed = min(trade.quantity, abs(net))
            direction = 1 if net > 0 else -1
            gross = before.gross_realised + Money(
                (trade.price.amount - before.average_price.amount) * closed * direction
            )
            if new_net == 0:
                average = _ZERO
            elif (new_net > 0) == (net > 0):
                average = before.average_price  # only reduced: the rest is still at the old cost
            else:
                average = trade.price  # flipped: the remainder opens at this fill's price
        return PositionState(trade.instrument_id, new_net, average, gross, before.fees + fees)
