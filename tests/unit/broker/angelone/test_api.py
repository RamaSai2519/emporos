"""EM-47: typed read API — request formatting and hardened response parsing."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from emporos.broker.angelone.api import (
    AngelOneApi,
    CandleInterval,
    QuoteMode,
    ResponseParser,
)
from emporos.broker.angelone.endpoints import Endpoints
from emporos.broker.angelone.models import (
    FundsResponse,
    LtpResponse,
    ProfileResponse,
    QuoteResponse,
)
from emporos.broker.errors import BrokerProtocolError
from emporos.core.clock import IST
from tests.support.fakes import ScriptedRestTransport

PARSER = ResponseParser()


def api_with(**script: list[object]) -> tuple[AngelOneApi, ScriptedRestTransport]:
    transport = ScriptedRestTransport(**script)
    return AngelOneApi(transport), transport


async def test_candle_bounds_are_sent_as_ist_wall_clock_regardless_of_input_zone() -> None:
    api, transport = api_with(getCandleData=[[]])

    await api.candles(
        "NSE",
        "3045",
        CandleInterval.FIVE_MINUTE,
        datetime(2026, 9, 18, 3, 45, tzinfo=UTC),  # 09:15 IST
        datetime(2026, 9, 18, 15, 30, tzinfo=IST),
    )

    (request,) = transport.sent_to("getCandleData")
    assert request.body == {
        "exchange": "NSE",
        "symboltoken": "3045",
        "interval": "FIVE_MINUTE",
        "fromdate": "2026-09-18 09:15",
        "todate": "2026-09-18 15:30",
    }


async def test_naive_candle_bounds_are_refused() -> None:
    api, transport = api_with()
    with pytest.raises(ValueError, match="timezone-aware"):
        await api.candles(
            "NSE", "3045", CandleInterval.ONE_DAY, datetime(2026, 9, 18), datetime.now(UTC)
        )
    assert transport.requests == []


def test_only_intervals_the_api_accepts_exist() -> None:
    assert {i.value for i in CandleInterval} == {
        "ONE_MINUTE", "THREE_MINUTE", "FIVE_MINUTE", "TEN_MINUTE",
        "FIFTEEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR", "ONE_DAY",
    }  # fmt: skip


async def test_candle_rows_become_utc_bars_with_decimal_prices() -> None:
    row = [
        "2026-09-18T09:15:00+05:30",
        Decimal("992.0"),
        Decimal("992.5"),
        Decimal("989.0"),
        Decimal("989.6"),
        35003,
    ]
    api, _ = api_with(getCandleData=[[row]])

    (bar,) = await api.candles(
        "NSE", "3045", CandleInterval.ONE_MINUTE, datetime.now(UTC), datetime.now(UTC)
    )

    assert bar.ts == datetime(2026, 9, 18, 3, 45, tzinfo=UTC)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        Decimal("992.0"), Decimal("992.5"), Decimal("989.0"), Decimal("989.6"), 35003,
    )  # fmt: skip
    assert all(isinstance(p, Decimal) for p in (bar.open, bar.high, bar.low, bar.close))


@pytest.mark.parametrize(
    "bad_row",
    [
        "not-a-row",
        ["2026-09-18T09:15:00+05:30", 1, 2, 3, 4],  # a column short
        ["2026-09-18T09:15:00+05:30", 1, 2, 3, 4, 5, 6],  # a column long
        ["yesterday", 1, 2, 3, 4, 5],  # unparseable timestamp
        ["2026-09-18T09:15:00", 1, 2, 3, 4, 5],  # no timezone: refuse to guess
        ["2026-09-18T09:15:00+05:30", "x", 2, 3, 4, 5],  # non-numeric price
        ["2026-09-18T09:15:00+05:30", 1, 2, 3, 4, "many"],  # non-numeric volume
        ["2026-09-18T09:15:00+05:30", 1, 2, 3, 4, 1.5],  # fractional volume
    ],
)
async def test_a_malformed_candle_row_is_a_typed_protocol_error(bad_row: object) -> None:
    api, _ = api_with(getCandleData=[[bad_row]])
    with pytest.raises(BrokerProtocolError):
        await api.candles(
            "NSE", "3045", CandleInterval.ONE_MINUTE, datetime.now(UTC), datetime.now(UTC)
        )


@pytest.mark.parametrize("not_a_list", [None, "x", {}, 5])
async def test_a_candle_reply_that_is_not_a_list_is_a_protocol_error(not_a_list: object) -> None:
    api, _ = api_with(getCandleData=[not_a_list])
    with pytest.raises(BrokerProtocolError):
        await api.candles(
            "NSE", "3045", CandleInterval.ONE_MINUTE, datetime.now(UTC), datetime.now(UTC)
        )


async def test_ltp_and_quote_requests_match_the_api_contract() -> None:
    api, transport = api_with(
        getLtpData=[
            {
                "exchange": "NSE",
                "tradingsymbol": "S",
                "symboltoken": "1",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1,
                "ltp": Decimal("1.5"),
            }
        ],
        quote=[{"fetched": [], "unfetched": []}],
    )  # fmt: skip

    ltp = await api.ltp("NSE", "SBIN-EQ", "3045")
    quotes = await api.quotes(QuoteMode.LTP, {"NSE": ("3045", "2885")})

    assert transport.sent_to("getLtpData")[0].body == {
        "exchange": "NSE", "tradingsymbol": "SBIN-EQ", "symboltoken": "3045"
    }  # fmt: skip
    assert transport.sent_to("quote")[0].body == {
        "mode": "LTP",
        "exchangeTokens": {"NSE": ["3045", "2885"]},
    }
    assert ltp.ltp == Decimal("1.5") and quotes.fetched == ()


async def test_validation_errors_name_fields_but_never_echo_values() -> None:
    api, _ = api_with(getRMS=[{"net": "SECRET-BALANCE-VALUE", "availablecash": "1.0"}])

    with pytest.raises(BrokerProtocolError) as raised:
        await api.funds()

    assert "net" in str(raised.value)
    assert "SECRET-BALANCE-VALUE" not in str(raised.value)
    assert "SECRET-BALANCE-VALUE" not in repr(raised.value.__cause__)  # chained cause suppressed


def test_personal_profile_fields_are_never_parsed() -> None:
    profile = PARSER.model(
        Endpoints.PROFILE,
        ProfileResponse,
        {
            "clientcode": "C1",
            "name": "A Person",
            "email": "a@b.c",
            "mobileno": "9",
            "exchanges": ["nse_cm"],
            "products": [],
        },
    )
    assert set(ProfileResponse.model_fields) == {"client_code", "exchanges", "products"}
    assert "A Person" not in repr(profile)


def test_funds_amounts_are_decimal_and_blank_extras_become_none() -> None:
    funds = PARSER.model(
        Endpoints.FUNDS,
        FundsResponse,
        {
            "net": "100000.00",
            "availablecash": "99999.5",
            "collateral": "",
            "m2mrealized": None,
            "extra_new_field": 1,
        },
    )
    assert funds.net == Decimal("100000.00") and funds.available_cash == Decimal("99999.5")
    assert funds.collateral is None and funds.m2m_realized is None


def test_models_are_immutable() -> None:
    funds = PARSER.model(Endpoints.FUNDS, FundsResponse, {"net": "1", "availablecash": "1"})
    with pytest.raises(Exception, match="frozen"):
        funds.net = Decimal(2)  # type: ignore[misc]


_JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=8),
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(max_size=8), children, max_size=4),
    max_leaves=12,
)


@given(data=_JSON)
def test_no_json_value_can_make_a_parser_raise_anything_but_a_protocol_error(data: Any) -> None:
    """Fuzz: arbitrary upstream JSON either parses or fails as a typed, ambiguous error."""
    for model in (ProfileResponse, FundsResponse, LtpResponse, QuoteResponse):
        with contextlib.suppress(BrokerProtocolError):
            PARSER.model(Endpoints.PROFILE, model, json.loads(json.dumps(data)))
    with contextlib.suppress(BrokerProtocolError):
        PARSER.candles(Endpoints.CANDLES, data)
