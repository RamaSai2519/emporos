"""`SimulatedBroker` — the backtest's exchange: a bar-driven order book (plan.md §10).

    strategy signal ─▶ (gate) ─▶ (pricing) ─▶ submit() ─▶ rests ─▶ match(bar) ─▶ fills

It is NOT a `Broker` (no login, quotes or streams): it is the small interface a backtest needs,
and it is deliberately not Angel One or the paper broker. Paper is tick-driven; this is
bar-driven, so only the accounting and cost pieces are shared with paper, not the matcher.

Look-ahead is closed HERE, by the clock and not by anything the strategy says: an order carries
the instant it was accepted (`Clock.now()`, which stands at the close of the bar the strategy was
answering) and a bar can fill it only if the bar OPENED at or after that instant. An order placed
on bar t therefore cannot trade on bar t, however the bar's range looks. A stop-limit is triggered
by one bar and can first fill on the next. Everything is synchronous and total-ordered: orders are
matched in the order they were placed, and share each bar's liquidity.

Orders are DAY orders: `expire_open_orders()` cancels what is left at the end of a session.
Not simulated: circuit limits, queue depth, cancel latency, margin (see EM-99).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.backtest.fill_model import BarFillModel
from emporos.backtest.orders import Fill, FillReason, SimEvent, SimOrderRequest
from emporos.backtest.rejects import NeverReject, RejectPolicy
from emporos.core.clock import Clock
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.order_updates import OrderUpdate, OrderUpdateStatus
from emporos.domain.orders import OrderSide, OrderType


class DuplicateTagError(ValueError):
    """An order with this client tag already exists. A resend is refused, never duplicated."""


class UnknownOrderError(LookupError):
    """No order has that id."""


class OrderNotOpenError(RuntimeError):
    """The order already reached a final state."""


@dataclass
class _Order:
    order_id: str
    request: SimOrderRequest
    accepted_at: datetime
    status: OrderUpdateStatus
    filled: int = 0
    turnover: Decimal = Decimal(0)  # sum of price * quantity over the fills
    armed_at: datetime | None = None  # a stop-limit: when its trigger fired, else None

    @property
    def remaining(self) -> int:
        return self.request.quantity - self.filled

    @property
    def is_open(self) -> bool:
        return not self.status.is_terminal


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: str
    request: SimOrderRequest
    filled: int
    status: OrderUpdateStatus


class SimulatedBroker:
    def __init__(
        self, clock: Clock, model: BarFillModel | None = None, rejects: RejectPolicy | None = None
    ) -> None:
        self._clock = clock
        self._model = model or BarFillModel()
        self._rejects: RejectPolicy = rejects or NeverReject()
        self._orders: dict[str, _Order] = {}
        # Only the orders still resting, in the order they were accepted, so a bar looks at the
        # handful that can trade instead of every order the run has ever placed.
        self._open: dict[str, _Order] = {}
        self._open_by_instrument: dict[str, dict[str, _Order]] = {}
        self._tags: set[str] = set()
        self._fills = 0

    # --- orders ---------------------------------------------------------------------------
    def submit(self, request: SimOrderRequest) -> SimEvent:
        """Accept an order (WORKING) or refuse it (REJECTED). A reused tag raises."""
        if request.tag in self._tags:
            raise DuplicateTagError(f"client tag {request.tag} was already used")
        self._tags.add(request.tag)
        order = _Order(self._next_order_id(), request, self._clock.now(), OrderUpdateStatus.WORKING)
        self._orders[order.order_id] = order
        why = self._rejects.rejection(request)
        if why is not None:
            order.status = OrderUpdateStatus.REJECTED
            return self._event(order, why)
        self._open[order.order_id] = order
        self._open_by_instrument.setdefault(request.instrument_id, {})[order.order_id] = order
        return self._event(order)

    def cancel(self, order_id: str) -> SimEvent:
        order = self._order(order_id)
        if not order.is_open:
            raise OrderNotOpenError(f"order {order_id} is {order.status}")
        order.status = OrderUpdateStatus.CANCELLED
        self._close(order)
        return self._event(order, "cancelled")

    def open_orders(self) -> tuple[OrderSnapshot, ...]:
        return tuple(self._snapshot(o) for o in self._open.values())

    def order(self, order_id: str) -> OrderSnapshot:
        return self._snapshot(self._order(order_id))

    # --- time -----------------------------------------------------------------------------
    def match(self, bar: Candle) -> list[SimEvent]:
        """Let one closed bar act on the resting orders. The clock stands at the bar's close."""
        capacity = self._model.capacity(bar)
        events: list[SimEvent] = []
        for order in self._working_in(bar.instrument_id):
            if order.accepted_at > bar.ts:
                continue  # placed after this bar opened: it can only trade on a LATER bar
            if self._still_waiting_for_trigger(order, bar):
                continue
            quantity = min(order.remaining, capacity)
            price = self._model.price_if_filled(order.request.side, order.request.limit_price, bar)
            if price is None or quantity <= 0:
                continue
            capacity -= quantity
            events.append(self._fill(order, quantity, price, FillReason.MATCHED))
        return events

    def expire_open_orders(self) -> list[SimEvent]:
        """End of session: every order still resting is cancelled (they are day orders)."""
        events = []
        for order in list(self._open.values()):
            order.status = OrderUpdateStatus.CANCELLED
            self._close(order)
            events.append(self._event(order, "expired at the end of the session"))
        return events

    def square_off(
        self, instrument_id: str, side: OrderSide, quantity: int, price: Money
    ) -> SimEvent:
        """The broker's own end-of-day square-off: it closes a position at `price` whether or not
        our orders did. Not an order type we can place, and recorded as `FORCED_SQUARE_OFF`."""
        request = SimOrderRequest(
            instrument_id, side, OrderType.LIMIT, quantity, price, f"SQUAREOFF-{self._fills + 1}"
        )
        order = _Order(self._next_order_id(), request, self._clock.now(), OrderUpdateStatus.WORKING)
        self._orders[order.order_id] = order
        self._tags.add(request.tag)
        return self._fill(order, quantity, price, FillReason.FORCED_SQUARE_OFF)

    # --- internals ------------------------------------------------------------------------
    def _working_in(self, instrument_id: str) -> list[_Order]:
        return list(self._open_by_instrument.get(instrument_id, {}).values())

    def _close(self, order: _Order) -> None:
        """The order reached a final state: it no longer rests."""
        self._open.pop(order.order_id, None)
        resting = self._open_by_instrument.get(order.request.instrument_id)
        if resting is not None:
            resting.pop(order.order_id, None)

    def _still_waiting_for_trigger(self, order: _Order, bar: Candle) -> bool:
        """A stop-limit rests untriggered until a bar reaches its trigger; that bar only ARMS it,
        so the earliest bar that can fill it is the next one."""
        request = order.request
        if request.order_type is not OrderType.STOPLOSS_LIMIT:
            return False
        if order.armed_at is None:
            assert request.trigger_price is not None
            if self._model.triggers(request.side, request.trigger_price, bar):
                order.armed_at = bar.closes_at
            return True
        return order.armed_at > bar.ts

    def _fill(self, order: _Order, quantity: int, price: Money, reason: FillReason) -> SimEvent:
        self._fills += 1
        order.filled += quantity
        order.turnover += price.amount * quantity
        order.status = (
            OrderUpdateStatus.FILLED if order.remaining == 0 else OrderUpdateStatus.PARTIALLY_FILLED
        )
        if order.status.is_terminal:
            self._close(order)
        fill = Fill(
            self._fills, order.order_id, order.request.tag, order.request.instrument_id,
            order.request.side, quantity, price, self._clock.now(), reason,
        )  # fmt: skip
        return self._event(order, fill=fill)

    def _event(self, order: _Order, message: str = "", fill: Fill | None = None) -> SimEvent:
        request = order.request
        update = OrderUpdate(
            instrument_id=request.instrument_id,
            side=request.side,
            status=order.status,
            quantity=request.quantity,
            filled_quantity=order.filled,
            ts=self._clock.now(),
            average_price=Money(order.turnover / order.filled) if order.filled else None,
            ordertag=request.tag,
            message=message,
        )
        return SimEvent(order.order_id, update, fill)

    def _snapshot(self, order: _Order) -> OrderSnapshot:
        return OrderSnapshot(order.order_id, order.request, order.filled, order.status)

    def _order(self, order_id: str) -> _Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise UnknownOrderError(f"no order {order_id}") from None

    def _next_order_id(self) -> str:
        return f"SIM-{len(self._orders) + 1:06d}"
