"""Typed SmartAPI responses, shaped from LIVE recordings (fixtures are authoritative — plan.md §4).

Money and prices are `Decimal`, never float. Only the fields we use are modelled: the profile's
name/email/mobile are deliberately *not* parsed, so personal data never enters the process
beyond the transport. Adapters (Phase 7) translate these into broker-neutral DTOs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from emporos.core.clock import IST

_EXCH_TIME_FORMAT = "%d-%b-%Y %H:%M:%S"  # e.g. "18-Sep-2026 15:59:57", IST


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


def _exchange_time(value: Any) -> Any:
    """Exchange timestamps arrive as IST wall-clock text; store them as UTC."""
    if isinstance(value, str):
        return datetime.strptime(value, _EXCH_TIME_FORMAT).replace(tzinfo=IST).astimezone(UTC)
    return value


OptionalDecimal = Annotated[Decimal | None, BeforeValidator(_blank_to_none)]
ExchangeTime = Annotated[datetime, BeforeValidator(_exchange_time)]


class _Response(BaseModel):
    """Frozen, and tolerant of new upstream fields (a new field must not break trading)."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class ProfileResponse(_Response):
    client_code: str = Field(alias="clientcode")
    exchanges: tuple[str, ...]
    products: tuple[str, ...]


class FundsResponse(_Response):
    """`getRMS`. Every amount is a decimal string upstream; the utilised* extras may be null."""

    net: Decimal
    available_cash: Decimal = Field(alias="availablecash")
    available_intraday_payin: OptionalDecimal = Field(default=None, alias="availableintradaypayin")
    available_limit_margin: OptionalDecimal = Field(default=None, alias="availablelimitmargin")
    collateral: OptionalDecimal = None
    m2m_unrealized: OptionalDecimal = Field(default=None, alias="m2munrealized")
    m2m_realized: OptionalDecimal = Field(default=None, alias="m2mrealized")
    utilised_debits: OptionalDecimal = Field(default=None, alias="utiliseddebits")
    utilised_payout: OptionalDecimal = Field(default=None, alias="utilisedpayout")


class LtpResponse(_Response):
    exchange: str
    trading_symbol: str = Field(alias="tradingsymbol")
    symbol_token: str = Field(alias="symboltoken")
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    ltp: Decimal


class QuoteEntry(_Response):
    exchange: str
    trading_symbol: str = Field(alias="tradingSymbol")
    symbol_token: str = Field(alias="symbolToken")
    ltp: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    trade_volume: int = Field(alias="tradeVolume")
    lower_circuit: Decimal = Field(alias="lowerCircuit")
    upper_circuit: Decimal = Field(alias="upperCircuit")
    exch_trade_time: ExchangeTime = Field(alias="exchTradeTime")


class QuoteResponse(_Response):
    fetched: tuple[QuoteEntry, ...]
    unfetched: tuple[Any, ...] = ()


class CandleBar(_Response):
    """One `getCandleData` row, `[iso_ts, open, high, low, close, volume]`, ts as UTC."""

    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
