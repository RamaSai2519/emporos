"""The simulated exchange: resting orders, their lifecycle, and matching against the tick stream.

Every change to an order — accepted, triggered, part-filled, filled, amended, cancelled,
rejected — is returned as an `ExchangeEvent` carrying the order's new state and a per-order,
monotonic sequence number. The caller journals and publishes the events; the exchange itself does
no I/O, reads no clock (it is told the time) and calls nothing outside its injected policies.

Assumptions (see `fills` for the policies): orders are eligible only for ticks that arrive after
their latency has elapsed; a marketable order does NOT fill on placement, only on a later tick;
ticks flagged out-of-order never fill anything; orders on one instrument share each tick's
liquidity in time priority.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from emporos.broker.errors import BrokerRejectedError
from emporos.broker.models import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerTrade,
    PlaceOrderRequest,
)
from emporos.broker.paper.fills import FillPolicy, LiquidityModel, TriggerRule, WorkingOrder
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderType
from emporos.domain.ticks import Tick

_WORKING = {
    BrokerOrderStatus.OPEN,
    BrokerOrderStatus.TRIGGER_PENDING,
    BrokerOrderStatus.PARTIALLY_FILLED,
}


@dataclass(frozen=True)
class ExchangeEvent:
    """An order's state after one change. `trade` is set when the change was a fill."""

    order: BrokerOrder
    seq: int
    at: datetime
    trade: BrokerTrade | None = None
    reason: str = ""


@dataclass(frozen=True)
class RestoredOrder:
    """An order as persisted, to be put back on the exchange after a restart."""

    order: BrokerOrder
    seq: int
    trades: int  # how many trades it has already had (so new trade ids continue the series)


class SimulatedOrder:
    """One order and its state machine. Terminal orders refuse every further change."""

    def __init__(self, order: BrokerOrder, seq: int, live_from: datetime, trades: int = 0) -> None:
        self._order = order
        self._seq = seq
        self._live_from = live_from
        self._trades = trades

    @property
    def snapshot(self) -> BrokerOrder:
        return self._order

    @property
    def is_working(self) -> bool:
        return self._order.status in _WORKING

    @property
    def awaiting_trigger(self) -> bool:
        return self._order.status is BrokerOrderStatus.TRIGGER_PENDING

    @property
    def view(self) -> WorkingOrder:
        order = self._order
        assert order.price is not None  # every order placed here has a limit price
        return WorkingOrder(
            order.side, order.price, order.quantity - order.filled_quantity, order.trigger_price
        )

    def eligible_at(self, when: datetime) -> bool:
        return when >= self._live_from

    def accepted(self, now: datetime) -> ExchangeEvent:
        return self._event(now, self._order)

    def rejected(self, now: datetime, reason: str) -> ExchangeEvent:
        return self._event(
            now, replace(self._order, status=BrokerOrderStatus.REJECTED, status_message=reason),
            reason,
        )  # fmt: skip

    def triggered(self, now: datetime) -> ExchangeEvent:
        self._require_working("trigger")
        return self._event(now, replace(self._order, status=BrokerOrderStatus.OPEN))

    def cancelled(self, now: datetime) -> ExchangeEvent:
        self._require_working("cancel")
        return self._event(now, replace(self._order, status=BrokerOrderStatus.CANCELLED))

    def amended(
        self, now: datetime, quantity: int, price: Money, trigger_price: Money | None
    ) -> ExchangeEvent:
        self._require_working("modify")
        if quantity <= self._order.filled_quantity:
            raise BrokerRejectedError(
                f"quantity {quantity} does not exceed the {self._order.filled_quantity} "
                "already filled"
            )
        if (trigger_price is None) != (self._order.trigger_price is None):
            raise BrokerRejectedError("a modification cannot add or remove the trigger price")
        return self._event(
            now,
            replace(self._order, quantity=quantity, price=price, trigger_price=trigger_price),
        )

    def filled(self, now: datetime, quantity: int, price: Money) -> ExchangeEvent:
        self._require_working("fill")
        order = self._order
        if not 0 < quantity <= order.quantity - order.filled_quantity:
            raise ValueError("a fill must be positive and no more than the unfilled quantity")
        total = order.filled_quantity + quantity
        previous = (order.average_price or Money.zero()).amount * order.filled_quantity
        average = Money((previous + price.amount * quantity) / total)
        status = (
            BrokerOrderStatus.FILLED
            if total == order.quantity
            else BrokerOrderStatus.PARTIALLY_FILLED
        )
        self._trades += 1
        trade = BrokerTrade(
            f"{order.broker_order_id}-{self._trades}",
            order.broker_order_id,
            order.instrument_id,
            order.side,
            quantity,
            price,
            now,
        )
        updated = replace(order, filled_quantity=total, status=status, average_price=average)
        return self._event(now, updated, trade=trade)

    def _event(
        self, now: datetime, order: BrokerOrder, reason: str = "", trade: BrokerTrade | None = None
    ) -> ExchangeEvent:
        self._order = replace(order, updated_at=now)
        self._seq += 1
        return ExchangeEvent(self._order, self._seq, now, trade, reason)

    def _require_working(self, action: str) -> None:
        if not self.is_working:
            raise BrokerRejectedError(
                f"cannot {action} order {self._order.broker_order_id}: it is "
                f"{self._order.status.value}"
            )


class TapeVolume:
    """Turns the feed's cumulative day volume into what traded since the previous tick."""

    def __init__(self) -> None:
        self._last: dict[str, int] = {}

    def since_previous(self, tick: Tick) -> int | None:
        if tick.volume is None:
            return None
        previous = self._last.get(tick.instrument_id)
        self._last[tick.instrument_id] = tick.volume
        if previous is None:
            return None  # the first tick has no baseline: nothing can be said about it
        return max(tick.volume - previous, 0)  # a falling counter is a feed glitch, not volume


class SimulatedExchange:
    def __init__(
        self,
        policy: FillPolicy,
        liquidity: LiquidityModel,
        trigger: TriggerRule,
        ids: IdGenerator,
        latency: timedelta = timedelta(0),
    ) -> None:
        if latency < timedelta(0):
            raise ValueError("latency cannot be negative")
        self._policy = policy
        self._liquidity = liquidity
        self._trigger = trigger
        self._ids = ids
        self._latency = latency
        self._orders: dict[str, SimulatedOrder] = {}  # insertion order is time priority
        self._tape = TapeVolume()

    # --- queries ---------------------------------------------------------------------------
    def orders(self) -> list[BrokerOrder]:
        return [o.snapshot for o in self._orders.values()]

    def working_orders(self) -> list[BrokerOrder]:
        return [o.snapshot for o in self._orders.values() if o.is_working]

    def find_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        return [o.snapshot for o in self._orders.values() if o.snapshot.client_tag == client_tag]

    def get(self, broker_order_id: str) -> BrokerOrder:
        return self._order(broker_order_id).snapshot

    # --- commands --------------------------------------------------------------------------
    def restore(self, restored: Sequence[RestoredOrder], now: datetime) -> None:
        """Put persisted orders back. Only valid on an empty exchange: a restart, not a merge."""
        if self._orders:
            raise ValueError("orders can only be restored onto an empty exchange")
        for item in restored:
            order = SimulatedOrder(item.order, item.seq, live_from=now, trades=item.trades)
            self._orders[item.order.broker_order_id] = order

    def submit(
        self, request: PlaceOrderRequest, now: datetime, reject_reason: str | None = None
    ) -> ExchangeEvent:
        """Accept an order (or, given a `reject_reason`, record it as rejected downstream)."""
        order_id = f"PAPER{self._ids.new_ulid()}"
        status = (
            BrokerOrderStatus.TRIGGER_PENDING
            if request.order_type is OrderType.STOPLOSS_LIMIT
            else BrokerOrderStatus.OPEN
        )
        order = BrokerOrder(
            broker_order_id=order_id,
            client_tag=request.client_tag,
            instrument_id=request.instrument_id,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            filled_quantity=0,
            status=status,
            price=request.price,
            trigger_price=request.trigger_price,
            updated_at=now,
        )
        simulated = SimulatedOrder(order, 0, live_from=now + self._latency)
        self._orders[order_id] = simulated
        if reject_reason is not None:
            return simulated.rejected(now, reject_reason)
        return simulated.accepted(now)

    def cancel(self, broker_order_id: str, now: datetime) -> ExchangeEvent:
        return self._order(broker_order_id).cancelled(now)

    def amend(
        self,
        broker_order_id: str,
        now: datetime,
        quantity: int,
        price: Money,
        trigger_price: Money | None,
    ) -> ExchangeEvent:
        return self._order(broker_order_id).amended(now, quantity, price, trigger_price)

    def on_tick(self, tick: Tick, now: datetime) -> list[ExchangeEvent]:
        """Match one tick against the resting orders on its instrument."""
        if tick.out_of_order:
            return []
        budget = self._liquidity.budget(self._tape.since_previous(tick))
        events: list[ExchangeEvent] = []
        for order in list(self._orders.values()):
            if (
                not order.is_working
                or order.snapshot.instrument_id != tick.instrument_id
                or not order.eligible_at(tick.received_ts)
            ):
                continue
            if order.awaiting_trigger:
                if self._trigger.fires(order.view, tick):
                    events.append(order.triggered(now))  # eligible to fill from the next tick
                continue
            price = self._policy.execution_price(order.view, tick)
            if price is None:
                continue
            quantity = order.view.remaining if budget is None else min(order.view.remaining, budget)
            if quantity <= 0:
                continue
            if budget is not None:
                budget -= quantity
            events.append(order.filled(now, quantity, price))
        return events

    def _order(self, broker_order_id: str) -> SimulatedOrder:
        try:
            return self._orders[broker_order_id]
        except KeyError:
            raise BrokerRejectedError(f"unknown order {broker_order_id!r}") from None


__all__ = ["ExchangeEvent", "RestoredOrder", "SimulatedExchange", "SimulatedOrder", "TapeVolume"]
