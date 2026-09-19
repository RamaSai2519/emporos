"""Live, read-only Angel One checks through the typed API (EM-47): profile, funds, quote, LTP
and historical candles. PLACES NO ORDERS — the dev key has no registered static IP, and only
order APIs are IP-gated.

Skipped unless ANGELONE_* credentials are set. Assertions are on derived booleans so a failure
can never echo account data into logs. One session per client code: see test_angelone_live_auth.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from emporos.broker.angelone.api import AngelOneApi, CandleInterval, QuoteMode
from emporos.broker.angelone.factory import AngelOneStack
from emporos.core.clock import SystemClock

pytestmark = pytest.mark.integration

SBIN_NSE_TOKEN = "3045"


@pytest.fixture
def api(angelone_stack: AngelOneStack) -> AngelOneApi:
    return AngelOneApi(angelone_stack.transport)


async def test_profile_and_funds_are_fetched_and_typed(api: AngelOneApi) -> None:
    profile = await api.profile()
    funds = await api.funds()

    has_identity = bool(profile.client_code) and "nse_cm" in profile.exchanges
    amounts_are_exact = isinstance(funds.net, Decimal) and isinstance(funds.available_cash, Decimal)
    assert has_identity
    assert amounts_are_exact


async def test_ltp_and_full_quote_agree_and_are_exact(api: AngelOneApi) -> None:
    ltp = await api.ltp("NSE", "SBIN-EQ", SBIN_NSE_TOKEN)
    quotes = await api.quotes(QuoteMode.FULL, {"NSE": [SBIN_NSE_TOKEN]})

    (entry,) = quotes.fetched
    prices_exact = all(isinstance(p, Decimal) for p in (ltp.ltp, ltp.open, entry.ltp))
    within_circuits = entry.lower_circuit < entry.ltp < entry.upper_circuit
    day_range_sane = ltp.low <= ltp.ltp <= ltp.high
    assert prices_exact
    assert within_circuits
    assert day_range_sane


async def test_historical_candles_are_ordered_utc_and_internally_consistent(
    api: AngelOneApi,
) -> None:
    end = SystemClock().now()

    bars = await api.candles(
        "NSE", SBIN_NSE_TOKEN, CandleInterval.ONE_DAY, end - timedelta(days=10), end
    )

    assert bars
    in_order = all(a.ts < b.ts for a, b in pairwise(bars))
    all_utc = all(b.ts.utcoffset() == timedelta(0) for b in bars)
    ohlc_consistent = all(b.low <= b.open <= b.high and b.low <= b.close <= b.high for b in bars)
    exact = all(isinstance(b.close, Decimal) for b in bars)
    assert in_order
    assert all_utc
    assert ohlc_consistent
    assert exact
