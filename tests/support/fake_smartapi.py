"""A STATEFUL emulation of SmartAPI's order and account endpoints, as a `RestTransport` double.

It is built from SmartAPI's documentation (the dev key cannot place orders, so nothing here was
observed live except one fact: an empty account answers `data: null` for the books, which this
reproduces). It enforces what the real exchange rules enforce — LIMIT / STOPLOSS_LIMIT only, DAY
only — so a request body that could place a forbidden order fails here exactly as it would there.

Its purpose is to let `AngelOneBroker` be exercised end to end: request bodies go in, wire-format
order books come out, and the adapter must round-trip them through its mapping."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count
from typing import Any

from emporos.broker.angelone.transport import RestRequest
from emporos.broker.errors import BrokerRejectedError, BrokerTransportError

ALLOWED_ORDER_TYPES = {"LIMIT", "STOPLOSS_LIMIT"}


class FakeSmartApi:
    def __init__(self, tokens: dict[str, str], now: Callable[[], datetime] | None = None) -> None:
        self._tokens = tokens  # symboltoken -> tradingsymbol
        self._now = now or (lambda: datetime(2026, 9, 21, 4, 30, tzinfo=UTC))
        self._ids = count(1)
        self.orders: dict[str, dict[str, Any]] = {}
        self.trades: list[dict[str, Any]] = []
        self.requests: list[RestRequest] = []
        self.lose_next_place_reply = False
        self.sessions_created = 0

    # --- test hook -------------------------------------------------------------------------
    def fill(self, order_id: str, quantity: int, price: Decimal) -> None:
        row = self.orders[order_id]
        filled = int(row["filledshares"]) + quantity
        row["filledshares"] = str(filled)
        row["averageprice"] = str(price)
        row["orderstatus"] = "complete" if filled >= int(row["quantity"]) else "open"
        self.trades.append(
            {
                "orderid": order_id,
                "fillid": f"F{len(self.trades) + 1}",
                "exchange": row["exchange"],
                "symboltoken": row["symboltoken"],
                "transactiontype": row["transactiontype"],
                "fillprice": str(price),
                "fillsize": str(quantity),
                "filltime": "10:00:00",
            }
        )

    # --- RestTransport ---------------------------------------------------------------------
    async def send(self, request: RestRequest) -> Any:
        self.requests.append(request)
        handler = getattr(self, f"_{request.endpoint.name}", None)
        if handler is None:
            raise AssertionError(f"FakeSmartApi does not implement {request.endpoint.name}")
        return handler(request.body or {})

    def _getProfile(self, body: dict[str, Any]) -> Any:
        return {
            "clientcode": "C1",
            "exchanges": ["nse_fo", "nse_cm", "bse_cm"],
            "products": ["MIS"],
        }

    def _getRMS(self, body: dict[str, Any]) -> Any:
        return {"net": "100000.00", "availablecash": "99000.00"}

    def _quote(self, body: dict[str, Any]) -> Any:
        fetched = []
        for exchange, tokens in body["exchangeTokens"].items():
            for token in tokens:
                fetched.append(
                    {
                        "exchange": exchange,
                        "tradingSymbol": self._tokens[token],
                        "symbolToken": token,
                        "ltp": 100.5,
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100,
                        "tradeVolume": 10,
                        "lowerCircuit": 90,
                        "upperCircuit": 110,
                        "exchTradeTime": "18-Sep-2026 15:59:57",
                    }
                )
        return {"fetched": fetched, "unfetched": []}

    def _placeOrder(self, body: dict[str, Any]) -> Any:
        self._enforce_exchange_rules(body)
        order_id = str(201000 + next(self._ids))
        self.orders[order_id] = {
            "orderid": order_id, "exchange": body["exchange"], "symboltoken": body["symboltoken"],
            "transactiontype": body["transactiontype"], "ordertype": body["ordertype"],
            "quantity": body["quantity"], "price": body["price"],
            "triggerprice": body.get("triggerprice", "0"), "ordertag": body.get("ordertag", ""),
            "orderstatus": "trigger pending" if body["ordertype"] == "STOPLOSS_LIMIT" else "open",
            "filledshares": "0", "averageprice": "0", "text": "",
            "updatetime": "21-Sep-2026 10:00:00",
        }  # fmt: skip
        if self.lose_next_place_reply:
            self.lose_next_place_reply = False
            raise BrokerTransportError("timeout: the reply was lost")
        return {
            "script": body["tradingsymbol"],
            "orderid": order_id,
            "uniqueorderid": f"u{order_id}",
        }

    def _modifyOrder(self, body: dict[str, Any]) -> Any:
        self._enforce_exchange_rules(body)
        row = self._row(body["orderid"])
        row.update(quantity=body["quantity"], price=body["price"])
        if "triggerprice" in body:
            row["triggerprice"] = body["triggerprice"]
        return {"orderid": row["orderid"]}

    def _cancelOrder(self, body: dict[str, Any]) -> Any:
        row = self._row(body["orderid"])
        row["orderstatus"] = "cancelled"
        return {"orderid": row["orderid"]}

    def _getOrderBook(self, body: dict[str, Any]) -> Any:
        return [dict(r) for r in self.orders.values()] or None  # recorded live: empty -> null

    def _getTradeBook(self, body: dict[str, Any]) -> Any:
        return [dict(t) for t in self.trades] or None

    def _getPosition(self, body: dict[str, Any]) -> Any:
        net: dict[tuple[str, str], int] = {}
        for t in self.trades:
            key = (t["exchange"], t["symboltoken"])
            signed = int(t["fillsize"]) * (1 if t["transactiontype"] == "BUY" else -1)
            net[key] = net.get(key, 0) + signed
        rows = [
            {"exchange": e, "symboltoken": tok, "netqty": str(q), "avgnetprice": "100.5"}
            for (e, tok), q in net.items()
            if q != 0
        ]
        return rows or None

    def _getHolding(self, body: dict[str, Any]) -> Any:
        return []

    # --- rules -----------------------------------------------------------------------------
    def _row(self, order_id: str) -> dict[str, Any]:
        if order_id not in self.orders:
            raise BrokerRejectedError(f"order {order_id} not found")
        return self.orders[order_id]

    @staticmethod
    def _enforce_exchange_rules(body: dict[str, Any]) -> None:
        """What the real gateway/exchange would refuse."""
        if body.get("ordertype") not in ALLOWED_ORDER_TYPES:
            raise BrokerRejectedError(f"order type {body.get('ordertype')!r} is prohibited")
        if body.get("duration") != "DAY":
            raise BrokerRejectedError("only DAY validity is allowed")
        if not isinstance(body.get("price"), str) or not isinstance(body.get("quantity"), str):
            raise BrokerRejectedError("price and quantity must be text")
