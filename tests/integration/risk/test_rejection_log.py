"""Rejections really land in `risk_events` on Atlas, with the state that was judged, and can be
found again by signal and by rule (EM-73). No order is placed: this exercises the engine only."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.marketdata.session import SessionWindow
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.repositories import RiskEventRepository
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.risk.approval import RiskApprovedSignal, RiskRejection
from emporos.risk.engine import RiskEngine
from emporos.risk.limits import RiskLimits
from emporos.risk.rejection_log import MongoRejectionLog
from emporos.risk.standard import StandardRuleSet
from tests.support.fakes import RecordingAlertSink
from tests.support.risk import NOW, StaticSnapshots, generous_limits, healthy
from tests.support.strategies import make_signal

pytestmark = pytest.mark.integration

Database = AsyncDatabase[Mapping[str, Any]]


@pytest.fixture
async def events(database: Database) -> AsyncIterator[RiskEventRepository]:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    yield RiskEventRepository(database)


def roomy_limits() -> RiskLimits:
    """Only the fat-finger cap can trip for a 500-plus share order."""
    return generous_limits(
        max_position_value=Decimal("1000000"), max_capital_deployed=Decimal("1000000")
    )


def engine_over(events: RiskEventRepository, snapshot_signal_run: str) -> RiskEngine:
    ids = IdGenerator()
    return RiskEngine(
        StandardRuleSet(roomy_limits(), SessionWindow()).rules(),
        StaticSnapshots(healthy()),
        MongoRejectionLog(events, ids),
        ids,
        FixedClock(NOW),
        RecordingAlertSink(),
    )


async def test_a_rejection_is_stored_with_its_rule_reason_trace_and_snapshot(
    events: RiskEventRepository,
) -> None:
    run = f"zz-run-{IdGenerator().new_ulid()}"
    signal_id = f"zz-sig-{IdGenerator().new_ulid()}"
    signal = make_signal(run_id=run, quantity=501, price="100.5", side=OrderSide.BUY)
    try:
        decision = await engine_over(events, run).review(signal, signal_id)

        assert isinstance(decision, RiskRejection)
        (stored,) = await events.for_signal(signal_id)
        assert stored.rule == "MaxOrderQuantityGuard" == decision.rule
        assert stored.reason == decision.reason and stored.ts == NOW
        assert (stored.strategy_run_id, stored.quantity, stored.side) == (run, 501, OrderSide.BUY)
        assert stored.limit_price == Money.of("100.5")
        assert stored.trace[-1] == {"rule": "MaxOrderQuantityGuard", "allowed": False,
                                    "reason": decision.reason}  # fmt: skip
        assert len(stored.trace) == 13  # every rule up to and including the one that blocked
        assert stored.snapshot["now"] == NOW.isoformat()
        assert stored.details == {"quantity": "501", "cap": "500"}
    finally:
        for record in await events.for_signal(signal_id):
            await events.delete(record.id)


async def test_a_rejection_can_be_found_by_rule_after_the_fact(
    events: RiskEventRepository,
) -> None:
    run = f"zz-run-{IdGenerator().new_ulid()}"
    signal_id = f"zz-sig-{IdGenerator().new_ulid()}"
    try:
        await engine_over(events, run).review(make_signal(run_id=run, quantity=9999), signal_id)
        found = [
            r for r in await events.for_rule("MaxOrderQuantityGuard") if r.signal_id == signal_id
        ]
        assert len(found) == 1
    finally:
        for record in await events.for_signal(signal_id):
            await events.delete(record.id)


async def test_an_approved_signal_writes_no_risk_event(events: RiskEventRepository) -> None:
    run = f"zz-run-{IdGenerator().new_ulid()}"
    signal_id = f"zz-sig-{IdGenerator().new_ulid()}"
    decision = await engine_over(events, run).review(make_signal(run_id=run), signal_id)
    assert isinstance(decision, RiskApprovedSignal)
    assert await events.for_signal(signal_id) == []
