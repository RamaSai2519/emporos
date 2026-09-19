"""A minimal reference `Broker`: pure Python, no I/O. It exists to prove the contract suite is
implementation-agnostic (it must pass for something that is not Angel One) and as a template for
`PaperBroker` / `SimulatedBroker`."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count

from emporos.broker.base import Broker
from emporos.broker.errors import BrokerRejectedError, BrokerTransportError
from emporos.broker.models import (
    BrokerHolding,
    BrokerOrder,
    BrokerOrderAck,
    BrokerOrderStatus,
    BrokerOrderUpdate,
    BrokerPosition,
    BrokerSession,
    BrokerTrade,
    CancelOrderRequest,
    CandleRequest,
    Funds,
    MarketDataMode,
    ModifyOrderRequest,
    PlaceOrderRequest,
    Profile,
    Quote,
)
from emporos.domain.candles import Candle
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.ticks import Tick

NOW = datetime(2026, 9, 21, 4, 30, tzinfo=UTC)


class InMemoryBroker(Broker):
    def __init__(self, instruments: Sequence[Instrument], history: list[Candle]) -> None:
        self._instruments = {i.instrument_id: i for i in instruments}
        self.history = history
        self._orders: dict[str, BrokerOrder] = {}
        self._trades: list[BrokerTrade] = []
        self._ids = count(1)
        self._logins = 0
        self._session: BrokerSession | None = None
        self._tick_handlers: list[Callable[[Tick], None]] = []
        self._update_handlers: list[Callable[[BrokerOrderUpdate], None]] = []
        self.lose_next_place_reply = False

    # --- test hooks ------------------------------------------------------------------------
    def emit_tick(self, tick: Tick) -> None:
        for handler in self._tick_handlers:
            try:
                handler(tick)
            except Exception:  # a broken handler must not starve the rest
                continue

    def fill(self, broker_order_id: str, quantity: int, price: Money) -> None:
        order = self._orders[broker_order_id]
        filled = order.filled_quantity + quantity
        status = (
            BrokerOrderStatus.FILLED
            if filled >= order.quantity
            else (BrokerOrderStatus.PARTIALLY_FILLED)
        )
        self._update(replace(order, filled_quantity=filled, status=status, average_price=price))
        self._trades.append(
            BrokerTrade(
                f"T{len(self._trades) + 1}", broker_order_id, order.instrument_id, order.side,
                quantity, price, NOW,
            )
        )  # fmt: skip

    # --- session ---------------------------------------------------------------------------
    async def authenticate(self) -> BrokerSession:
        self._logins += 1
        self._session = BrokerSession("C1", NOW, NOW + timedelta(hours=12, minutes=self._logins))
        return self._session

    async def ensure_session(self) -> BrokerSession:
        return self._session or await self.authenticate()

    async def logout(self) -> None:
        self._session = None

    async def get_profile(self) -> Profile:
        return Profile("C1", (Exchange.NSE, Exchange.BSE))

    async def get_instruments(self) -> Sequence[Instrument]:
        return list(self._instruments.values())

    # --- market data -----------------------------------------------------------------------
    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        for i in instrument_ids:
            self._locate(i)
        p = Money.of("100.00")
        return [
            Quote(i, p, p, p, p, p, 10, NOW, Money.of("90"), Money.of("110"))
            for i in instrument_ids
        ]

    async def get_historical_candles(self, request: CandleRequest) -> list[Candle]:
        self._locate(request.instrument_id)
        bars = {
            c.ts: c
            for c in self.history
            if c.instrument_id == request.instrument_id
            and c.timeframe is request.timeframe
            and request.start <= c.ts < request.end
        }
        return [bars[ts] for ts in sorted(bars)]

    async def subscribe_market_data(
        self, instrument_ids: Sequence[str], mode: MarketDataMode
    ) -> None:
        for i in instrument_ids:
            self._locate(i)

    async def unsubscribe_market_data(self, instrument_ids: Sequence[str]) -> None:
        for i in instrument_ids:
            self._locate(i)

    def on_tick(self, handler: Callable[[Tick], None]) -> None:
        self._tick_handlers.append(handler)

    # --- orders ----------------------------------------------------------------------------
    async def place_order(self, request: PlaceOrderRequest) -> BrokerOrderAck:
        self._locate(request.instrument_id)
        order_id = str(1000 + next(self._ids))
        status = (
            BrokerOrderStatus.OPEN
            if request.trigger_price is None
            else BrokerOrderStatus.TRIGGER_PENDING
        )
        self._update(
            BrokerOrder(
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
                updated_at=NOW,
            )
        )
        if self.lose_next_place_reply:
            self.lose_next_place_reply = False
            raise BrokerTransportError("timeout: the reply was lost")
        return BrokerOrderAck(order_id, request.client_tag)

    async def modify_order(self, request: ModifyOrderRequest) -> BrokerOrderAck:
        order = self._order(request.broker_order_id)
        self._update(
            replace(
                order,
                quantity=request.quantity,
                price=request.price,
                trigger_price=request.trigger_price,
            )
        )
        return BrokerOrderAck(order.broker_order_id)

    async def cancel_order(self, request: CancelOrderRequest) -> BrokerOrderAck:
        order = self._order(request.broker_order_id)
        self._update(replace(order, status=BrokerOrderStatus.CANCELLED))
        return BrokerOrderAck(order.broker_order_id)

    async def get_order_book(self) -> list[BrokerOrder]:
        return list(self._orders.values())

    async def get_trade_book(self) -> list[BrokerTrade]:
        return list(self._trades)

    async def find_orders_by_tag(self, client_tag: str) -> list[BrokerOrder]:
        if not client_tag:
            raise ValueError("a client tag is required")
        return [o for o in self._orders.values() if o.client_tag == client_tag]

    def on_order_update(self, handler: Callable[[BrokerOrderUpdate], None]) -> None:
        self._update_handlers.append(handler)

    # --- account ---------------------------------------------------------------------------
    async def get_positions(self) -> list[BrokerPosition]:
        net: dict[str, tuple[int, Money]] = {}
        for trade in self._trades:
            signed = trade.quantity if trade.side is OrderSide.BUY else -trade.quantity
            qty, _ = net.get(trade.instrument_id, (0, trade.price))
            net[trade.instrument_id] = (qty + signed, trade.price)
        return [BrokerPosition(i, q, p) for i, (q, p) in net.items() if q != 0]

    async def get_holdings(self) -> list[BrokerHolding]:
        return []

    async def get_funds(self) -> Funds:
        return Funds(Money.of("100000.00"), Money.of("99000.00"))

    # --- internals -------------------------------------------------------------------------
    def _locate(self, instrument_id: str) -> Instrument:
        try:
            return self._instruments[instrument_id]
        except KeyError:
            raise BrokerRejectedError(f"unknown instrument {instrument_id!r}") from None

    def _order(self, broker_order_id: str) -> BrokerOrder:
        try:
            return self._orders[broker_order_id]
        except KeyError:
            raise BrokerRejectedError(f"unknown order {broker_order_id!r}") from None

    def _update(self, order: BrokerOrder) -> None:
        self._orders[order.broker_order_id] = order
        update = BrokerOrderUpdate(order, NOW)
        for handler in self._update_handlers:
            try:
                handler(update)
            except Exception:
                continue
