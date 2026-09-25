"""Broker-neutral DTOs: the vocabulary every `Broker` implementation speaks (plan.md §5).

Nothing here names a broker or its wire format: no session secrets, no wire-format field names.
Each adapter translates to and from its own protocol.

Two safety properties are structural rather than checked at runtime:

* a forbidden order is *unrepresentable* — `OrderType` (domain) has no MARKET or IOC, and
  `Validity` has only DAY (Decision 8);
* money is `Money` (Decimal), never a float.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from emporos.domain.candles import Timeframe
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType

# The broker echoes this tag on its order book and its order-update stream; it is the idempotency
# handle (Decision 7). The length limit is [VOLATILE — verify against the live API].
MAX_CLIENT_TAG_LENGTH = 20
_CLIENT_TAG = re.compile(r"^[A-Za-z0-9]{1,%d}$" % MAX_CLIENT_TAG_LENGTH)


def _require_utc(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None)):
        raise ValueError(f"{name} must be timezone-aware UTC")


class ProductType(StrEnum):
    INTRADAY = "INTRADAY"  # cash intraday: squared off the same day
    DELIVERY = "DELIVERY"


class Validity(StrEnum):
    """DAY only: IOC is prohibited for algo orders, so it cannot be expressed."""

    DAY = "DAY"


class MarketDataMode(StrEnum):
    """Feed richness, as a minimum: an implementation may deliver more (QUOTE satisfies LTP)."""

    LTP = "LTP"
    QUOTE = "QUOTE"


class BrokerOrderStatus(StrEnum):
    PENDING = "PENDING"  # accepted by the broker, not yet at the exchange
    OPEN = "OPEN"
    TRIGGER_PENDING = "TRIGGER_PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNRECOGNISED = "UNRECOGNISED"  # a status we have no mapping for: never guessed as "open"

    @property
    def is_terminal(self) -> bool:
        return self in {self.FILLED, self.CANCELLED, self.REJECTED}


@dataclass(frozen=True)
class BrokerSession:
    """A session's lifetime, with none of its secrets."""

    client_code: str
    established_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_utc("established_at", self.established_at)
        _require_utc("expires_at", self.expires_at)


@dataclass(frozen=True)
class Profile:
    client_code: str
    exchanges: tuple[Exchange, ...]


@dataclass(frozen=True)
class Quote:
    instrument_id: str
    ltp: Money
    open: Money
    high: Money
    low: Money
    close: Money
    volume: int | None = None
    exchange_ts: datetime | None = None
    lower_circuit: Money | None = None
    upper_circuit: Money | None = None
    # Best bid and ask: None when that side of the book is empty or the broker did not send depth.
    bid: Money | None = None
    ask: Money | None = None
    # The quantities resting at the best bid and ask (EM-217): None when that side is empty.
    bid_qty: int | None = None
    ask_qty: int | None = None

    def __post_init__(self) -> None:
        _require_utc("exchange_ts", self.exchange_ts)


@dataclass(frozen=True)
class CandleRequest:
    instrument_id: str
    timeframe: Timeframe
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_utc("start", self.start)
        _require_utc("end", self.end)
        if self.start >= self.end:
            raise ValueError("a candle request needs start < end")


def _require_enum(name: str, value: object, kind: type[StrEnum]) -> None:
    """A forbidden value (e.g. the string "MARKET") must fail at runtime too, not only in mypy."""
    if not isinstance(value, kind):
        raise TypeError(f"{name} must be a {kind.__name__}, got {value!r}")


def _check_tag(tag: str) -> None:
    if not _CLIENT_TAG.match(tag):
        raise ValueError(f"client_tag must be 1-{MAX_CLIENT_TAG_LENGTH} alphanumeric characters")


def _check_prices(order_type: OrderType, price: Money, trigger_price: Money | None) -> None:
    if price <= Money.zero():
        raise ValueError("a limit price must be positive")
    if order_type is OrderType.STOPLOSS_LIMIT:
        if trigger_price is None or trigger_price <= Money.zero():
            raise ValueError("a stop-loss limit order needs a positive trigger price")
    elif trigger_price is not None:
        raise ValueError("only stop-loss limit orders take a trigger price")


@dataclass(frozen=True)
class PlaceOrderRequest:
    """`client_tag` is the idempotency handle, minted and persisted BEFORE this is sent."""

    instrument_id: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Money
    client_tag: str
    product: ProductType = ProductType.INTRADAY
    validity: Validity = Validity.DAY
    trigger_price: Money | None = None

    def __post_init__(self) -> None:
        _require_enum("side", self.side, OrderSide)
        _require_enum("order_type", self.order_type, OrderType)
        _require_enum("product", self.product, ProductType)
        _require_enum("validity", self.validity, Validity)
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        _check_prices(self.order_type, self.price, self.trigger_price)
        _check_tag(self.client_tag)


@dataclass(frozen=True)
class ModifyOrderRequest:
    """Repricing is cancel-then-replace, never modify-in-place (CLAUDE.md): the execution engine
    must not use this to chase a price. It exists for the interface's completeness."""

    broker_order_id: str
    instrument_id: str
    order_type: OrderType
    quantity: int
    price: Money
    trigger_price: Money | None = None

    def __post_init__(self) -> None:
        if not self.broker_order_id:
            raise ValueError("a broker order id is required")
        _require_enum("order_type", self.order_type, OrderType)
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        _check_prices(self.order_type, self.price, self.trigger_price)


@dataclass(frozen=True)
class CancelOrderRequest:
    broker_order_id: str
    order_type: OrderType

    def __post_init__(self) -> None:
        if not self.broker_order_id:
            raise ValueError("a broker order id is required")
        _require_enum("order_type", self.order_type, OrderType)


@dataclass(frozen=True)
class BrokerOrderAck:
    """The broker accepted the request. It is NOT a fill and not even proof the order is live."""

    broker_order_id: str
    client_tag: str | None = None


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_tag: str | None
    instrument_id: str
    side: OrderSide
    # None: a type this platform cannot place (e.g. a market order entered in the broker's app).
    order_type: OrderType | None
    quantity: int
    filled_quantity: int
    status: BrokerOrderStatus
    price: Money | None = None
    trigger_price: Money | None = None
    average_price: Money | None = None
    status_message: str = ""
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc("updated_at", self.updated_at)
        if self.quantity < 0 or self.filled_quantity < 0:
            raise ValueError("quantities cannot be negative")


@dataclass(frozen=True)
class BrokerOrderUpdate:
    """A pushed order-state change: the order as it now stands."""

    order: BrokerOrder
    received_at: datetime

    def __post_init__(self) -> None:
        _require_utc("received_at", self.received_at)


@dataclass(frozen=True)
class BrokerTrade:
    trade_id: str
    broker_order_id: str
    instrument_id: str
    side: OrderSide
    quantity: int
    price: Money
    executed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc("executed_at", self.executed_at)
        if self.quantity <= 0:
            raise ValueError("a trade has a positive quantity")


@dataclass(frozen=True)
class BrokerPosition:
    instrument_id: str
    net_quantity: int
    average_price: Money
    ltp: Money | None = None
    realized_pnl: Money | None = None
    unrealized_pnl: Money | None = None


@dataclass(frozen=True)
class BrokerHolding:
    instrument_id: str
    quantity: int
    average_price: Money
    ltp: Money | None = None


@dataclass(frozen=True)
class Funds:
    net: Money
    available_cash: Money
    utilised: Money | None = None
    realized_pnl: Money | None = None
    unrealized_pnl: Money | None = None
