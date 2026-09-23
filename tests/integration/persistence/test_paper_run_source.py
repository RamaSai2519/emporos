"""`MongoPaperRunSource` on real Atlas (EM-185): a paper run's records, by signal and run linkage.
Every id is a made-up `em185-src-*` one and removed afterwards; no real run has them."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.cli.parity_composition import MongoPaperRunSource
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.persistence.collections import Collection
from emporos.persistence.records import (
    ExecutionRecord,
    OrderEventRecord,
    OrderRecord,
    RiskEventRecord,
    SignalRecord,
    StrategyRunRecord,
)
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderEventRepository,
    OrderRepository,
    RiskEventRepository,
    SignalRepository,
    StrategyRunRepository,
)

pytestmark = pytest.mark.integration

RUN = "em185-src-run"
DAY = "2001-01-02"  # a day no real session ever had
NOW = datetime(2001, 1, 2, 4, tzinfo=UTC)


@pytest.fixture
async def source(database: AsyncDatabase[Mapping[str, Any]]) -> AsyncIterator[MongoPaperRunSource]:
    runs, signals = StrategyRunRepository(database), SignalRepository(database)
    orders, events = OrderRepository(database), OrderEventRepository(database)
    executions, risk = ExecutionRepository(database), RiskEventRepository(database)
    await runs.insert(
        StrategyRunRecord(_id=RUN, strategy_id="em185-src-s", session_date=DAY, created_at=NOW)
    )
    await signals.insert(
        SignalRecord(
            _id="em185-src-sig", strategy_run_id=RUN, instrument_id="NSE:TESTQ99", ts=NOW,
            kind="ENTRY", side=OrderSide.BUY, order_type=OrderType.LIMIT, price=Money.of("10"),
            quantity=5, sequence=1, reason="t",
        )
    )  # fmt: skip
    await orders.insert(
        OrderRecord(
            _id="em185-src-ord", signal_id="em185-src-sig", idempotency_key="em185-src-key",
            ordertag="em185-src-tag", instrument_id="NSE:TESTQ99", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, quantity=5, limit_price=Money.of("10"), state="FILLED",
            session_date=DAY, created_at=NOW, updated_at=NOW,
        )
    )  # fmt: skip
    await events.insert(
        OrderEventRecord(_id="em185-src-ev", order_id="em185-src-ord", seq=1, ts=NOW, state="OPEN")
    )
    await executions.insert(
        ExecutionRecord(
            _id="em185-src-x", broker_trade_id="em185-src-trade", order_id="em185-src-ord",
            instrument_id="NSE:TESTQ99", side=OrderSide.BUY, quantity=5, price=Money.of("10"),
            ts=NOW,
        )
    )  # fmt: skip
    await risk.insert(RiskEventRecord(_id="em185-src-risk", rule="r", ts=NOW, strategy_run_id=RUN))
    try:
        yield MongoPaperRunSource(database)
    finally:
        await database[Collection.STRATEGY_RUNS].delete_many({"_id": RUN})
        await database[Collection.SIGNALS].delete_many({"_id": "em185-src-sig"})
        await database[Collection.ORDERS].delete_many({"_id": "em185-src-ord"})
        await database[Collection.ORDER_EVENTS].delete_many({"_id": "em185-src-ev"})
        await database[Collection.EXECUTIONS].delete_many({"_id": "em185-src-x"})
        await database[Collection.RISK_EVENTS].delete_many({"_id": "em185-src-risk"})


async def test_a_run_is_found_by_its_day_and_loads_every_record_it_owns(
    source: MongoPaperRunSource,
) -> None:
    (run,) = await source.runs_on(DAY)
    assert run.id == RUN

    data = await source.load(run)

    assert [s.id for s in data.signals] == ["em185-src-sig"]
    assert [o.id for o in data.orders] == ["em185-src-ord"]
    assert [e.id for e in data.order_events] == ["em185-src-ev"]
    assert [x.id for x in data.executions] == ["em185-src-x"]
    assert [r.id for r in data.risk_events] == ["em185-src-risk"]
    assert await source.runs_on("2001-01-03") == ()
