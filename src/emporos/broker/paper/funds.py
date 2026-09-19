"""Simulated funds and margin: what the account holds, what is committed, what is free.

Margin is `notional / leverage` on the quantity an order would OPEN (the part that closes an
existing position needs none). Working orders reserve margin for their unfilled quantity, so two
orders cannot both spend the same cash. The leverage is configuration, not a broker fact: Angel
One's real intraday margins vary by instrument and are not modelled.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from emporos.broker.models import BrokerOrder, Funds, PlaceOrderRequest
from emporos.broker.paper.account import PaperAccount
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.ticks import Tick

_ZERO = Money.zero()


class LastPrices:
    """The latest traded price per instrument, from the tick stream."""

    def __init__(self) -> None:
        self._last: dict[str, Money] = {}

    def observe(self, tick: Tick) -> None:
        if not tick.out_of_order:
            self._last[tick.instrument_id] = tick.ltp

    def last(self, instrument_id: str) -> Money | None:
        return self._last.get(instrument_id)


class WorkingOrders(Protocol):
    def working_orders(self) -> Sequence[BrokerOrder]: ...


class PaperFunds:
    def __init__(
        self,
        account: PaperAccount,
        working: WorkingOrders,
        prices: LastPrices,
        leverage: Decimal,
    ) -> None:
        if leverage < Decimal(1):
            raise ValueError("leverage is at least 1")
        self._account = account
        self._working = working
        self._prices = prices
        self._leverage = leverage

    def margin_required(self, request: PlaceOrderRequest) -> Money:
        position = self._account.position(request.instrument_id)
        net = 0 if position is None else position.net_quantity
        opposing = -net if request.side is OrderSide.BUY else net
        opening = request.quantity - min(request.quantity, max(opposing, 0))
        return Money(request.price.amount * opening / self._leverage)

    def unrealised(self) -> Money:
        total = _ZERO
        for position in self._account.positions():
            mark = self._prices.last(position.instrument_id)
            if mark is not None and position.net_quantity != 0:
                total = total + Money(
                    (mark.amount - position.average_price.amount) * position.net_quantity
                )
        return total

    def unrealised_for(self, instrument_id: str) -> Money | None:
        position = self._account.position(instrument_id)
        mark = self._prices.last(instrument_id)
        if position is None or mark is None:
            return None
        return Money((mark.amount - position.average_price.amount) * position.net_quantity)

    def utilised(self) -> Money:
        total = _ZERO
        for position in self._account.positions():
            total = total + Money(
                abs(position.net_quantity) * position.average_price.amount / self._leverage
            )
        for order in self._working.working_orders():
            if order.price is not None:
                remaining = order.quantity - order.filled_quantity
                total = total + Money(order.price.amount * remaining / self._leverage)
        return total

    def available_cash(self) -> Money:
        return self._account.cash - self.utilised()

    def funds(self) -> Funds:
        unrealised = self.unrealised()
        return Funds(
            net=self._account.cash + unrealised,
            available_cash=self.available_cash(),
            utilised=self.utilised(),
            realized_pnl=self._account.realised,
            unrealized_pnl=unrealised,
        )
