"""Typed, authenticated calls to the SmartAPI read endpoints (Phase 4 scope: no orders).

Sits on any `RestTransport` — in production the fully wired stack, in tests a fixture replay.
A reply that does not fit its model becomes a `BrokerProtocolError` (ambiguous) built from field
*names* only: validation messages quote values, and values can be account data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from emporos.broker.angelone.endpoints import Endpoint, Endpoints
from emporos.broker.angelone.models import (
    CandleBar,
    FundsResponse,
    LtpResponse,
    ProfileResponse,
    QuoteResponse,
)
from emporos.broker.angelone.transport import RestRequest, RestTransport
from emporos.broker.errors import BrokerProtocolError
from emporos.core.clock import IST

M = TypeVar("M", bound=BaseModel)

_CANDLE_TIME_FORMAT = "%Y-%m-%d %H:%M"  # IST wall clock, as the API expects


class CandleInterval(StrEnum):
    """The only intervals the API accepts; anything else is unrepresentable (it 400s)."""

    ONE_MINUTE = "ONE_MINUTE"
    THREE_MINUTE = "THREE_MINUTE"
    FIVE_MINUTE = "FIVE_MINUTE"
    TEN_MINUTE = "TEN_MINUTE"
    FIFTEEN_MINUTE = "FIFTEEN_MINUTE"
    THIRTY_MINUTE = "THIRTY_MINUTE"
    ONE_HOUR = "ONE_HOUR"
    ONE_DAY = "ONE_DAY"


class QuoteMode(StrEnum):
    LTP = "LTP"
    OHLC = "OHLC"
    FULL = "FULL"


class ResponseParser:
    """Validates `data` against a model, turning any failure into a typed protocol error."""

    def model(self, endpoint: Endpoint, model: type[M], data: Any) -> M:
        try:
            return model.model_validate(data)
        except ValidationError as error:
            raise BrokerProtocolError(
                f"{endpoint.name}: reply does not match the expected shape ({_fields(error)})"
            ) from None

    def candles(self, endpoint: Endpoint, data: Any) -> list[CandleBar]:
        if not isinstance(data, list):
            raise BrokerProtocolError(f"{endpoint.name}: candle reply is not a list")
        return [self._candle(endpoint, row) for row in data]

    def _candle(self, endpoint: Endpoint, row: Any) -> CandleBar:
        if not isinstance(row, list) or len(row) != 6:
            raise BrokerProtocolError(f"{endpoint.name}: candle row is not [ts, o, h, l, c, v]")
        try:
            ts = datetime.fromisoformat(str(row[0]))
        except ValueError:
            raise BrokerProtocolError(f"{endpoint.name}: candle timestamp is unparseable") from None
        if ts.tzinfo is None:
            raise BrokerProtocolError(f"{endpoint.name}: candle timestamp has no timezone")
        return self.model(
            endpoint,
            CandleBar,
            {
                "ts": ts.astimezone(UTC),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            },
        )


def _fields(error: ValidationError) -> str:
    names = sorted(
        {".".join(str(part) for part in item["loc"]) or "<root>" for item in error.errors()}
    )
    return "bad fields: " + ", ".join(names)


class AngelOneApi:
    def __init__(self, transport: RestTransport, parser: ResponseParser | None = None) -> None:
        self._transport = transport
        self._parser = parser or ResponseParser()

    async def profile(self) -> ProfileResponse:
        data = await self._transport.send(RestRequest(Endpoints.PROFILE))
        return self._parser.model(Endpoints.PROFILE, ProfileResponse, data)

    async def funds(self) -> FundsResponse:
        data = await self._transport.send(RestRequest(Endpoints.FUNDS))
        return self._parser.model(Endpoints.FUNDS, FundsResponse, data)

    async def ltp(self, exchange: str, trading_symbol: str, token: str) -> LtpResponse:
        body = {"exchange": exchange, "tradingsymbol": trading_symbol, "symboltoken": token}
        data = await self._transport.send(RestRequest(Endpoints.LTP, body=body))
        return self._parser.model(Endpoints.LTP, LtpResponse, data)

    async def quotes(
        self, mode: QuoteMode, exchange_tokens: Mapping[str, Sequence[str]]
    ) -> QuoteResponse:
        body = {
            "mode": mode.value,
            "exchangeTokens": {k: list(v) for k, v in exchange_tokens.items()},
        }
        data = await self._transport.send(RestRequest(Endpoints.QUOTE, body=body))
        return self._parser.model(Endpoints.QUOTE, QuoteResponse, data)

    async def candles(
        self,
        exchange: str,
        token: str,
        interval: CandleInterval,
        start: datetime,
        end: datetime,
    ) -> list[CandleBar]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("candle bounds must be timezone-aware")
        body = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval.value,
            "fromdate": start.astimezone(IST).strftime(_CANDLE_TIME_FORMAT),
            "todate": end.astimezone(IST).strftime(_CANDLE_TIME_FORMAT),
        }
        data = await self._transport.send(RestRequest(Endpoints.CANDLES, body=body))
        return self._parser.candles(Endpoints.CANDLES, data)
