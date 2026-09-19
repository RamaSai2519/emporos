"""EM-54: broker history (recorded `getCandleData` fixture) -> domain 1m candles for recovery."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill
from emporos.broker.angelone.transport import HttpRestTransport, RestRequest
from emporos.broker.errors import BrokerProtocolError
from emporos.core.clock import IST
from emporos.domain.candles import Timeframe
from emporos.domain.money import Money
from tests.support.angelone_fixtures import FixtureLibrary
from tests.support.fakes import ScriptedHttpServer, make_instrument

CANDLES = FixtureLibrary().get("candles_1m")
SBIN = make_instrument("3045", symbol="SBIN-EQ")
START, END = datetime(2026, 9, 18, 9, 15, tzinfo=IST), datetime(2026, 9, 18, 9, 20, tzinfo=IST)


class Authed:
    def __init__(self, inner: HttpRestTransport) -> None:
        self._inner = inner

    async def send(self, request: RestRequest) -> Any:
        return await self._inner.send(replace(request, bearer="t"))


def backfill(server: ScriptedHttpServer) -> AngelOneCandleBackfill:
    return AngelOneCandleBackfill(AngelOneApi(Authed(HttpRestTransport(server.client(), "k"))))


async def test_the_recorded_history_becomes_exact_utc_domain_candles() -> None:
    server = ScriptedHttpServer().queue(CANDLES.response())

    candles = await backfill(server).fetch_minutes(SBIN, START, END)

    assert len(candles) == 5
    first = candles[0]
    assert (first.instrument_id, first.timeframe) == ("NSE:3045", Timeframe.M1)
    assert first.ts == datetime(2026, 9, 18, 3, 45, tzinfo=UTC)  # 09:15 IST
    assert (first.open, first.close) == (Money.of("992.0"), Money.of("989.6"))
    assert first.volume == 35003 and first.partial is False  # flagging is recovery's job
    assert all(isinstance(c.close.amount, Decimal) for c in candles)
    body = json.loads(server.requests[0].content)
    assert (body["exchange"], body["symboltoken"], body["interval"]) == (
        "NSE",
        "3045",
        "ONE_MINUTE",
    )
    assert (body["fromdate"], body["todate"]) == ("2026-09-18 09:15", "2026-09-18 09:20")


async def test_an_inconsistent_candle_from_the_broker_is_a_typed_error() -> None:
    row = ["2026-09-18T09:15:00+05:30", 100, 99, 101, 100, 10]  # high below low
    body = {"status": True, "message": "SUCCESS", "errorcode": "", "data": [row]}
    server = ScriptedHttpServer().queue(httpx.Response(200, content=json.dumps(body).encode()))

    with pytest.raises(BrokerProtocolError):
        await backfill(server).fetch_minutes(SBIN, START, END)
