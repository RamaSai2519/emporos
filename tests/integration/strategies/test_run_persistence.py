"""The strategy run, its config snapshot and its signals, through the REAL repositories on the
shared Atlas `emporos_dev`. Scratch names and ids; only our own rows are deleted."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.clock import SystemClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import SignalKind
from emporos.persistence.repositories import (
    SignalRepository,
    StrategyRepository,
    StrategyRunRepository,
)
from emporos.session.strategy_runs import StrategyRunLauncher
from emporos.signals.recorder import SignalRecorder
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.resolution import StrategyConfigResolver
from tests.support.strategies import (
    INSTRUMENT,
    INSTRUMENT_MASTER,
    T0,
    ThresholdStrategy,
    changed,
    make_signal,
    raw_config,
)

pytestmark = pytest.mark.integration


class Scratch:
    """A uniquely-named strategy and every row this test creates for it."""

    def __init__(self, database: AsyncDatabase[Mapping[str, Any]]) -> None:
        self.name = f"zz_scratch_{uuid.uuid4().hex[:10]}"
        strategy_class = type("ScratchStrategy", (ThresholdStrategy,), {"name": self.name})
        self.registry = StrategyRegistry()
        self.registry.register(strategy_class)
        self.strategies = StrategyRepository(database)
        self.runs = StrategyRunRepository(database)
        self.signals = SignalRepository(database)
        self.launcher = StrategyRunLauncher(
            self.registry, self.strategies, self.runs, SystemClock(), IdGenerator()
        )
        self.run_ids: list[str] = []

    def config(self, raw: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
        raw = (raw or raw_config()) | {"name": self.name}
        return StrategyConfigResolver(self.registry, INSTRUMENT_MASTER).resolve(raw)

    async def cleanup(self) -> None:
        for run_id in self.run_ids:
            await self.signals.delete_many([s.id for s in await self.signals.for_run(run_id)])
        await self.runs.delete_many(self.run_ids)
        record = await self.strategies.get_by_name(self.name)
        if record is not None:
            await self.strategies.delete(record.id)


@pytest.fixture
async def scratch(database: AsyncDatabase[Mapping[str, Any]]) -> AsyncIterator[Scratch]:
    scratch = Scratch(database)
    try:
        yield scratch
    finally:
        await scratch.cleanup()


async def test_a_run_and_its_snapshot_survive_a_round_trip_through_atlas(scratch: Scratch) -> None:
    started = await scratch.launcher.start(scratch.config(), "2026-01-05")
    scratch.run_ids.append(started.run_id)

    stored = await scratch.runs.get(started.run_id)
    assert stored is not None
    assert stored.config_hash == started.snapshot.content_hash
    assert stored.config_snapshot == started.snapshot.document  # BSON changed nothing

    reproduced = await scratch.launcher.load(started.run_id)
    assert reproduced.config == started.config
    assert reproduced.snapshot == started.snapshot


async def test_the_strategy_catalogue_entry_is_unique_and_holds_the_latest_config(
    scratch: Scratch,
) -> None:
    first = await scratch.launcher.start(scratch.config(), "2026-01-05")
    edited = scratch.config(changed(raw_config(), "parameters.threshold", "250"))
    second = await scratch.launcher.start(edited, "2026-01-06")
    scratch.run_ids += [first.run_id, second.run_id]

    entries = await scratch.strategies.find({"name": scratch.name})
    assert len(entries) == 1
    assert entries[0].config == second.snapshot.document
    stored = [await scratch.runs.get(r) for r in (first.run_id, second.run_id)]
    assert {r.strategy_id for r in stored if r} == {entries[0].id}


async def test_two_runs_starting_at_once_share_one_catalogue_entry(scratch: Scratch) -> None:
    """The unique `name` index is what stops a duplicate; the launcher adopts the winner."""
    # pymongo 4.9.1 hangs when several tasks make their FIRST operation on a cold client at once
    # (EM-99 H1); one awaited round trip opens the pool, and the race below is unaffected.
    await scratch.strategies.count()
    started = await asyncio.gather(
        *(scratch.launcher.start(scratch.config(), "2026-01-05") for _ in range(4))
    )
    scratch.run_ids += [s.run_id for s in started]

    assert len(await scratch.strategies.find({"name": scratch.name})) == 1
    stored = [await scratch.runs.get(s.run_id) for s in started]
    assert len({r.strategy_id for r in stored if r}) == 1


async def test_signals_persist_in_order_with_exact_money_and_typed_enums(
    scratch: Scratch,
) -> None:
    started = await scratch.launcher.start(scratch.config(), "2026-01-05")
    scratch.run_ids.append(started.run_id)
    recorder = SignalRecorder(scratch.signals, IdGenerator())
    entry = make_signal(run_id=started.run_id, price="1234.55", quantity=12, ts=T0)
    exit_ = make_signal(
        run_id=started.run_id,
        kind=SignalKind.EXIT,
        side=OrderSide.SELL,
        price="1240.05",
        quantity=12,
        ts=T0,  # the same instant: only the sequence orders them
        reason="cross down",
    )

    for signal in (entry, exit_, make_signal(run_id=started.run_id, ts=T0 + timedelta(minutes=5))):
        await recorder.submit(signal)

    records = await scratch.signals.for_run(started.run_id)
    assert [r.sequence for r in records] == [1, 2, 3]
    assert [r.kind for r in records] == ["ENTRY", "EXIT", "ENTRY"]
    assert records[0].price == Money.of("1234.55") and records[1].price == Money.of("1240.05")
    assert records[1].side is OrderSide.SELL and records[0].order_type is OrderType.LIMIT
    assert records[1].reason == "cross down" and records[0].instrument_id == INSTRUMENT
