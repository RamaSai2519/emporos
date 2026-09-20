"""The API composition root must never wake the SSE stream on a MongoDB change stream (EM-137).

A pymongo 4.9.1 async change stream pins one pooled connection for its whole lifetime (an
endless long-poll `getMore`) on the SAME client the read repositories share, so the pool
stays at a connection or two and every request queues behind the stream until the
five-second server-selection timeout — the dashboard reads time out. The wake is documented
as an optimisation, never a dependency; the API wakes on a plain one-second poll.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase

from emporos.cli.api_composition import ApiComposer
from emporos.control.wake import PollingWake
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, FixedClock
from emporos.core.ids import IdGenerator
from emporos.risk.limits import RiskLimits


class _StubCollection:
    """The repositories only hold a collection reference at build time; nothing is queried."""


class _StubDatabase:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def __getitem__(self, name: str) -> _StubCollection:
        return _StubCollection()

    async def command(self, name: str, *args: object, **kwargs: object) -> dict[str, object]:
        self.commands.append(name)
        return {"ok": 1.0}


def _composer(database: _StubDatabase) -> ApiComposer:
    stub = cast(AsyncDatabase[Mapping[str, Any]], database)
    return ApiComposer(
        database=stub,
        clock=FixedClock(datetime(2026, 9, 18, 4, 0, tzinfo=UTC)),
        sleeper=AsyncioSleeper(),
        ids=IdGenerator(),
        alerts=LogAlertSink(),
        account_id="ACC",
        jwt_secret=b"k" * 32,
        limits=RiskLimits(
            max_daily_loss=Decimal("10000"),
            max_strategy_loss=Decimal("1000"),
            max_position_value=Decimal("50000"),
            max_open_positions=5,
            max_capital_deployed=Decimal("100000"),
            max_order_quantity=500,
            max_price_deviation_pct=Decimal("2"),
            max_spread_bps=Decimal("5"),
            duplicate_window_seconds=1,
            max_orders_per_second=10,
            max_orders_per_minute=300,
        ),
    )


def test_the_api_wake_is_a_plain_poll_not_a_change_stream() -> None:
    database = _StubDatabase()

    services, _ = _composer(database).build()

    assert isinstance(services.wake, PollingWake)


async def test_the_api_lifespan_only_warms_the_pool() -> None:
    database = _StubDatabase()
    _, lifespan = _composer(database).build()

    async with lifespan(cast(FastAPI, None)):  # the lifespan never reads its app argument
        pass

    assert database.commands == ["ping"]
