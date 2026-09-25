"""Domain <-> SmartAPI translation. The ONLY place broker-neutral DTOs meet SmartAPI field names.

Two kinds of data flow through here with different trust:

* OUT (order requests): built from validated DTOs, so a MARKET/IOC order cannot be produced — the
  neutral types cannot express one. The request-body field names follow SmartAPI's documentation
  and the SDK's parameter names; they have NOT been exercised live (orders are accepted only from
  the production host's registered static IP, 65.0.238.146, and none has been attempted from it).
* IN (books, positions, updates): tolerant. A listing must never fail because one entry has an
  order type we cannot place (orders entered in the broker's app) or a timestamp we cannot read;
  unknown statuses become `UNRECOGNISED`, never a guess.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from emporos.broker.angelone.models import (
    DepthLevel,
    FundsResponse,
    HoldingEntry,
    OrderBookEntry,
    PositionEntry,
    ProfileResponse,
    QuoteEntry,
    TradeBookEntry,
)
from emporos.broker.errors import BrokerRejectedError
from emporos.broker.models import (
    BrokerHolding,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPosition,
    BrokerTrade,
    CancelOrderRequest,
    Funds,
    ModifyOrderRequest,
    PlaceOrderRequest,
    ProductType,
    Profile,
    Quote,
)
from emporos.core.clock import IST
from emporos.domain.instruments import (
    Exchange,
    Instrument,
    InstrumentResolver,
    UnknownInstrumentError,
)
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType

_LOG = logging.getLogger(__name__)
_TIME_FORMAT = "%d-%b-%Y %H:%M:%S"  # e.g. "18-Sep-2026 15:59:57", IST wall clock

_VARIETY = {OrderType.LIMIT: "NORMAL", OrderType.STOPLOSS_LIMIT: "STOPLOSS"}
_ORDER_TYPE_OUT = {OrderType.LIMIT: "LIMIT", OrderType.STOPLOSS_LIMIT: "STOPLOSS_LIMIT"}
_ORDER_TYPE_IN = {"LIMIT": OrderType.LIMIT, "STOPLOSS_LIMIT": OrderType.STOPLOSS_LIMIT}
_PRODUCT = {ProductType.INTRADAY: "INTRADAY", ProductType.DELIVERY: "DELIVERY"}
_SIDE_OUT = {OrderSide.BUY: "BUY", OrderSide.SELL: "SELL"}
_SIDE_IN = {"BUY": OrderSide.BUY, "SELL": OrderSide.SELL}


def instrument_id_of(exchange: str, token: str) -> str:
    """`"{exchange}:{token}"` — derivable from a broker row alone, so an order or position in a
    segment we do not model (e.g. F&O) still gets a stable id."""
    return f"{exchange.upper()}:{token}"


def parse_exchange_time(text: str | None) -> datetime | None:
    """IST wall-clock text -> UTC; anything unreadable is `None` (never an error)."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), _TIME_FORMAT).replace(tzinfo=IST).astimezone(UTC)
    except ValueError:
        return None


def _money(value: Decimal | None) -> Money | None:
    return None if value is None else Money(value)


def _money_or_none_if_zero(value: Decimal | None) -> Money | None:
    """SmartAPI writes 0 where a field does not apply (no trigger on a plain limit, no average
    price before a fill). The neutral DTO says "not applicable" as None, so a tag-resolved order
    compares equal to the intent it was placed from."""
    return None if not value else Money(value)


def _best(levels: tuple[DepthLevel, ...]) -> Money | None:
    """The top of one side of the book. SmartAPI pads an empty level with price 0, quantity 0."""
    for level in levels:
        if level.price > 0 and level.quantity > 0:
            return Money(level.price)
    return None


def _best_quantity(levels: tuple[DepthLevel, ...]) -> int | None:
    """The quantity resting at the level `_best` picks."""
    for level in levels:
        if level.price > 0 and level.quantity > 0:
            return level.quantity
    return None


def _price_text(value: Money) -> str:
    return format(value.amount, "f")


class InstrumentLocator:
    """`instrument_id` -> the `Instrument` whose exchange/token/symbol the request needs."""

    def __init__(self, resolver: InstrumentResolver) -> None:
        self._resolver = resolver

    def locate(self, instrument_id: str) -> Instrument:
        try:
            return self._resolver.by_id(instrument_id)
        except UnknownInstrumentError:
            raise BrokerRejectedError(f"unknown instrument {instrument_id!r}") from None


class OrderRequestMapper:
    """Neutral order requests -> SmartAPI request bodies."""

    def __init__(self, locator: InstrumentLocator) -> None:
        self._locator = locator

    def place(self, request: PlaceOrderRequest) -> dict[str, str]:
        instrument = self._locator.locate(request.instrument_id)
        body = {
            "variety": _VARIETY[request.order_type],
            "tradingsymbol": instrument.tradingsymbol,
            "symboltoken": instrument.token,
            "transactiontype": _SIDE_OUT[request.side],
            "exchange": instrument.exchange.value,
            "ordertype": _ORDER_TYPE_OUT[request.order_type],
            "producttype": _PRODUCT[request.product],
            "duration": request.validity.value,
            "price": _price_text(request.price),
            "quantity": str(request.quantity),
            "ordertag": request.client_tag,
        }
        if request.trigger_price is not None:
            body["triggerprice"] = _price_text(request.trigger_price)
        return body

    def modify(self, request: ModifyOrderRequest) -> dict[str, str]:
        instrument = self._locator.locate(request.instrument_id)
        body = {
            "variety": _VARIETY[request.order_type],
            "orderid": request.broker_order_id,
            "ordertype": _ORDER_TYPE_OUT[request.order_type],
            "producttype": _PRODUCT[ProductType.INTRADAY],
            "duration": "DAY",
            "price": _price_text(request.price),
            "quantity": str(request.quantity),
            "tradingsymbol": instrument.tradingsymbol,
            "symboltoken": instrument.token,
            "exchange": instrument.exchange.value,
        }
        if request.trigger_price is not None:
            body["triggerprice"] = _price_text(request.trigger_price)
        return body

    @staticmethod
    def cancel(request: CancelOrderRequest) -> dict[str, str]:
        return {"variety": _VARIETY[request.order_type], "orderid": request.broker_order_id}


_STATUS_TABLE: Mapping[str, BrokerOrderStatus] = {
    "complete": BrokerOrderStatus.FILLED,
    "cancelled": BrokerOrderStatus.CANCELLED,
    "rejected": BrokerOrderStatus.REJECTED,
    "open": BrokerOrderStatus.OPEN,
    "modified": BrokerOrderStatus.OPEN,
    "trigger pending": BrokerOrderStatus.TRIGGER_PENDING,
    "validation pending": BrokerOrderStatus.PENDING,
    "put order req received": BrokerOrderStatus.PENDING,
    "open pending": BrokerOrderStatus.PENDING,
    "modify validation pending": BrokerOrderStatus.PENDING,
    "modify order request received": BrokerOrderStatus.PENDING,
    "after market order req received": BrokerOrderStatus.PENDING,
}


class OrderStatusMapper:
    """SmartAPI order-status text -> `BrokerOrderStatus`. Data-driven; unknown -> UNRECOGNISED."""

    def map(self, text: str | None, filled: int, quantity: int) -> BrokerOrderStatus:
        status = _STATUS_TABLE.get((text or "").strip().lower(), BrokerOrderStatus.UNRECOGNISED)
        if status is BrokerOrderStatus.OPEN and 0 < filled < quantity:
            return BrokerOrderStatus.PARTIALLY_FILLED
        return status


class AccountMapper:
    """SmartAPI books and account payloads -> neutral DTOs."""

    def __init__(self, statuses: OrderStatusMapper | None = None) -> None:
        self._statuses = statuses or OrderStatusMapper()

    def order(self, entry: OrderBookEntry) -> BrokerOrder:
        side = _SIDE_IN.get(entry.transaction_type.upper())
        if side is None:
            raise BrokerRejectedError(f"order {entry.order_id}: unknown side")
        filled = entry.filled_shares or 0
        return BrokerOrder(
            broker_order_id=entry.order_id,
            client_tag=entry.order_tag,
            instrument_id=instrument_id_of(entry.exchange, entry.symbol_token),
            side=side,
            order_type=_ORDER_TYPE_IN.get((entry.order_type or "").upper()),
            quantity=entry.quantity,
            filled_quantity=filled,
            status=self._statuses.map(entry.order_status or entry.status, filled, entry.quantity),
            price=_money(entry.price),
            trigger_price=_money_or_none_if_zero(entry.trigger_price),
            average_price=_money_or_none_if_zero(entry.average_price),
            status_message=entry.text or "",
            updated_at=parse_exchange_time(entry.update_time),
        )

    @staticmethod
    def trade(entry: TradeBookEntry) -> BrokerTrade:
        side = _SIDE_IN.get(entry.transaction_type.upper())
        if side is None:
            raise BrokerRejectedError(f"trade {entry.fill_id}: unknown side")
        return BrokerTrade(
            trade_id=entry.fill_id,
            broker_order_id=entry.order_id,
            instrument_id=instrument_id_of(entry.exchange, entry.symbol_token),
            side=side,
            quantity=entry.fill_size,
            price=Money(entry.fill_price),
            executed_at=parse_exchange_time(entry.fill_time),
        )

    @staticmethod
    def position(entry: PositionEntry) -> BrokerPosition:
        average = (
            entry.average_net_price if entry.average_net_price is not None else entry.net_price
        )
        return BrokerPosition(
            instrument_id=instrument_id_of(entry.exchange, entry.symbol_token),
            net_quantity=entry.net_quantity,
            average_price=Money(average if average is not None else Decimal(0)),
            ltp=_money(entry.ltp),
            realized_pnl=_money(entry.realised),
            unrealized_pnl=_money(entry.unrealised),
        )

    @staticmethod
    def holding(entry: HoldingEntry) -> BrokerHolding:
        return BrokerHolding(
            instrument_id=instrument_id_of(entry.exchange, entry.symbol_token),
            quantity=entry.quantity,
            average_price=Money(entry.average_price),
            ltp=_money(entry.ltp),
        )

    @staticmethod
    def funds(response: FundsResponse) -> Funds:
        return Funds(
            net=Money(response.net),
            available_cash=Money(response.available_cash),
            utilised=_money(response.utilised_debits),
            realized_pnl=_money(response.m2m_realized),
            unrealized_pnl=_money(response.m2m_unrealized),
        )

    @staticmethod
    def profile(response: ProfileResponse) -> Profile:
        known = {e.value.lower(): e for e in Exchange}
        exchanges = tuple(
            known[name.split("_")[0]]
            for name in response.exchanges
            if name.endswith("_cm") and name.split("_")[0] in known
        )  # cash segments only: nse_cm -> NSE, bse_cm -> BSE
        return Profile(client_code=response.client_code, exchanges=exchanges)

    @staticmethod
    def quote(entry: QuoteEntry) -> Quote:
        return Quote(
            instrument_id=instrument_id_of(entry.exchange, entry.symbol_token),
            ltp=Money(entry.ltp),
            open=Money(entry.open),
            high=Money(entry.high),
            low=Money(entry.low),
            close=Money(entry.close),
            volume=entry.trade_volume,
            exchange_ts=entry.exch_trade_time,
            lower_circuit=Money(entry.lower_circuit),
            upper_circuit=Money(entry.upper_circuit),
            bid=_best(entry.depth.buy if entry.depth else ()),
            ask=_best(entry.depth.sell if entry.depth else ()),
            bid_qty=_best_quantity(entry.depth.buy if entry.depth else ()),
            ask_qty=_best_quantity(entry.depth.sell if entry.depth else ()),
            open_interest=entry.open_interest,
        )


def order_from_update_data(data: Mapping[str, Any], mapper: AccountMapper) -> BrokerOrder:
    """An order-update stream `orderData` object -> the order as it now stands."""
    entry = OrderBookEntry.model_validate(
        {
            "orderid": data.get("orderid"),
            "exchange": data.get("exchange"),
            "symboltoken": data.get("symboltoken"),
            "transactiontype": data.get("transactiontype"),
            "quantity": data.get("quantity"),
            **{k: v for k, v in data.items() if k in _UPDATE_PASSTHROUGH},
        }
    )
    return mapper.order(entry)


_UPDATE_PASSTHROUGH = frozenset(
    {
        "ordertype", "filledshares", "price", "triggerprice", "averageprice", "ordertag",
        "orderstatus", "status", "text", "updatetime",
    }
)  # fmt: skip
