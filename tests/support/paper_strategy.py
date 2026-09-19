"""A small strategy and driver for proving the paper path end to end (Phase 9 builds the real
Strategy framework; this uses only the `Broker` interface, exactly as a strategy will).

`DipScalp`: when the price dips to its entry level, buy with a limit order; once filled, rest a
profit-target sell AND a stop-loss-limit sell; when one fills, cancel the other (an OCO pair).
The strategy only DECIDES — it emits intents from synchronous handlers. `StrategyDriver` executes
them (placing an order is async) in order, recording every signal first."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from emporos.broker.base import Broker
from emporos.broker.models import (
    BrokerOrderStatus,
    BrokerOrderUpdate,
    CancelOrderRequest,
    PlaceOrderRequest,
)
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.ticks import Tick


class Intent(StrEnum):
    ENTER = "ENTER"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    CANCEL_TARGET = "CANCEL_TARGET"
    CANCEL_STOP = "CANCEL_STOP"


@dataclass(frozen=True)
class Signal:
    kind: Intent
    instrument_id: str
    price: Money
    quantity: int
    at: datetime  # when the market event that caused it happened
    ordertag: str | None = None


class SignalSink(Protocol):
    async def record(self, signal: Signal) -> None: ...


class ListSignals:
    def __init__(self) -> None:
        self.signals: list[Signal] = []

    async def record(self, signal: Signal) -> None:
        self.signals.append(signal)


class DipScalp:
    def __init__(
        self, instrument_id: str, entry: Money, target: Money, stop_trigger: Money,
        stop_limit: Money, quantity: int,
    ) -> None:  # fmt: skip
        self._instrument_id = instrument_id
        self._entry, self._target = entry, target
        self._stop_trigger, self._stop_limit = stop_trigger, stop_limit
        self._quantity = quantity
        self._state = "IDLE"
        self._tags: dict[str, str] = {}  # role -> client tag, once the driver has placed it
        self.intents: deque[tuple[Intent, Money, datetime]] = deque()

    @property
    def done(self) -> bool:
        return self._state == "DONE"

    def bind(self, role: str, tag: str) -> None:
        self._tags[role] = tag

    @property
    def instrument_id(self) -> str:
        return self._instrument_id

    def tag_of(self, role: str) -> str | None:
        return self._tags.get(role)

    def role_of(self, tag: str | None) -> str | None:
        return next((role for role, t in self._tags.items() if t == tag), None)

    def on_tick(self, tick: Tick) -> None:
        if (
            self._state == "IDLE"
            and tick.instrument_id == self._instrument_id
            and tick.ltp <= self._entry
        ):
            self._state = "ENTERING"
            self.intents.append((Intent.ENTER, tick.ltp, tick.exchange_ts))

    def on_order_update(self, update: BrokerOrderUpdate) -> None:
        order = update.order
        role = self.role_of(order.client_tag)
        if order.status is not BrokerOrderStatus.FILLED:
            return
        at = update.received_at
        if role == "entry" and self._state == "ENTERING":
            self._state = "ENTERED"
            self.intents.append((Intent.TAKE_PROFIT, self._target, at))
            self.intents.append((Intent.STOP_LOSS, self._stop_limit, at))
        elif role == "target" and self._state == "ENTERED":
            self._state = "DONE"
            self.intents.append((Intent.CANCEL_STOP, self._stop_limit, at))
        elif role == "stop" and self._state == "ENTERED":
            self._state = "DONE"
            self.intents.append((Intent.CANCEL_TARGET, self._target, at))

    @property
    def stop_trigger(self) -> Money:
        return self._stop_trigger

    @property
    def quantity(self) -> int:
        return self._quantity


class StrategyDriver:
    def __init__(
        self, broker: Broker, strategy: DipScalp, signals: SignalSink, ids: IdGenerator,
        emit: Callable[[Tick], None],
    ) -> None:  # fmt: skip
        self._broker, self._strategy, self._signals = broker, strategy, signals
        self._ids, self._emit = ids, emit
        self._instrument_id = strategy.instrument_id
        self._orders: dict[str, str] = {}  # role -> broker order id
        broker.on_tick(strategy.on_tick)
        broker.on_order_update(strategy.on_order_update)

    async def run(self, ticks: Iterable[Tick]) -> None:
        for tick in ticks:
            self._emit(tick)  # the market speaks; the broker fills; the strategy decides
            await self._pump()

    async def _pump(self) -> None:
        while self._strategy.intents:
            intent, price, at = self._strategy.intents.popleft()
            await self._execute(intent, price, at)

    async def _execute(self, intent: Intent, price: Money, at: datetime) -> None:
        s, quantity = self._strategy, self._strategy.quantity
        if intent in {Intent.CANCEL_TARGET, Intent.CANCEL_STOP}:
            role = "target" if intent is Intent.CANCEL_TARGET else "stop"
            await self._signals.record(
                Signal(intent, self._instrument_id, price, quantity, at, s.tag_of(role))
            )
            await self._broker.cancel_order(
                CancelOrderRequest(self._orders[role], self._order_type(role))
            )
            return
        role = {Intent.ENTER: "entry", Intent.TAKE_PROFIT: "target", Intent.STOP_LOSS: "stop"}[
            intent
        ]
        tag = "S" + self._ids.new_ulid()[-12:]  # a compact alphanumeric idempotency handle
        s.bind(role, tag)
        await self._signals.record(Signal(intent, self._instrument_id, price, quantity, at, tag))
        side = OrderSide.BUY if role == "entry" else OrderSide.SELL
        stop = role == "stop"
        request = PlaceOrderRequest(
            self._instrument_id, side,
            OrderType.STOPLOSS_LIMIT if stop else OrderType.LIMIT,
            quantity, price, tag, trigger_price=s.stop_trigger if stop else None,
        )  # fmt: skip
        ack = await self._broker.place_order(request)
        self._orders[role] = ack.broker_order_id

    @staticmethod
    def _order_type(role: str) -> OrderType:
        return OrderType.STOPLOSS_LIMIT if role == "stop" else OrderType.LIMIT
