"""EM-109 — the trading worker runs a whole paper session, in virtual time, on real Atlas.

The worker is composed by the production composer; the market, the strategy and the clock are
scripted. What is asserted is the behaviour that matters when money is real: every signal is on
record and risk-gated, a restart never duplicates an order, a discrepancy stops trading, and the
day ends flat, reconciled and snapshotted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.persistence.collections import Collection
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.records import PositionRecord
from emporos.session.lifecycle import SessionState
from emporos.session.square_off import SQUARE_OFF_RUN
from tests.support.worker_rig import DAY, Crash, WorkerWorld, at, cleanup

pytestmark = pytest.mark.integration


@pytest.fixture
async def world(dev_settings: Any, tmp_path: Any) -> AsyncIterator[WorkerWorld]:
    mongo = MongoClientFactory(dev_settings)
    suffix = IdGenerator().new_ulid().lower()
    w = WorkerWorld(
        mongo=mongo,
        client=mongo.client,
        account_id=f"WORKERIT{suffix.upper()}",
        sentinel_path=tmp_path / "HALT",
        kill_switch_collection=f"zz_kill_switch_{suffix}",
    )
    from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
    from emporos.persistence.schema import PLATFORM_SCHEMA

    await MigrationRunner(MongoSchemaStore(mongo.database()), PLATFORM_SCHEMA).apply()
    try:
        yield w
    finally:
        await cleanup(w)
        await mongo.close()


async def rows(world: WorkerWorld, collection: str, **query: Any) -> list[dict[str, Any]]:
    return [d async for d in world.mongo.database()[collection].find(query)]


class TestAnOrdinarySession:
    async def test_enters_squares_off_and_ends_flat_reconciled_and_snapshotted(
        self, world: WorkerWorld
    ) -> None:
        assembly, _ = await world.build()
        report = await assembly.worker.run_session()

        assert report.final_state is SessionState.SHUTTING_DOWN and report.failure == ""
        assert report.traded and report.recovery is not None and report.recovery.clean
        assert report.flat_at_close is True
        assert report.square_off is not None and report.square_off.submitted == ("NSE:3045",)
        assert report.close_out is not None and report.close_out.discrepancies == 0

        orders = await rows(world, Collection.ORDERS, account_id=world.account_id)
        assert sorted((o["side"], o["quantity"], o["state"]) for o in orders) == [
            ("BUY", 100, "FILLED"), ("SELL", 100, "FILLED"),
        ]  # fmt: skip
        by_side = {o["side"]: o for o in orders}
        assert by_side["BUY"]["signal_kind"] == "ENTRY" and by_side["SELL"]["signal_kind"] == "EXIT"
        assert by_side["SELL"]["strategy_run_id"] == SQUARE_OFF_RUN
        (position,) = await rows(world, Collection.POSITIONS, account_id=world.account_id)
        assert position["net_quantity"] == 0

    async def test_every_signal_is_on_record_and_names_its_order(self, world: WorkerWorld) -> None:
        assembly, _ = await world.build()
        await assembly.worker.run_session()
        orders = {
            o["ordertag"]: o
            for o in await rows(world, Collection.ORDERS, account_id=world.account_id)
        }
        signals = await rows(
            world, Collection.SIGNALS, strategy_run_id={"$in": [r.run_id for r in assembly.runs]}
        )
        assert len(signals) == 1 and signals[0]["kind"] == "ENTRY"
        assert orders[signals[0]["ordertag"]]["signal_id"] == signals[0]["_id"]
        square_off = await rows(world, Collection.SIGNALS, strategy_run_id=SQUARE_OFF_RUN)
        assert any(s["kind"] == "EXIT" and s["ordertag"] in orders for s in square_off)
        for row in square_off:  # tidy: the square-off run id is shared, so remove only ours
            await world.mongo.database()[Collection.SIGNALS].delete_one({"_id": row["_id"]})

    async def test_the_paper_broker_and_the_platform_agree_order_for_order(
        self, world: WorkerWorld
    ) -> None:
        assembly, _ = await world.build()
        await assembly.worker.run_session()
        ours = await rows(world, Collection.ORDERS, account_id=world.account_id)
        theirs = await rows(world, Collection.PAPER_ORDERS, account_id=world.account_id)
        assert sorted(o["ordertag"] for o in ours) == sorted(o["ordertag"] for o in theirs)
        assert len(ours) == 2  # one entry, one exit: nothing duplicated

    async def test_the_lifecycle_and_reconciliations_are_recorded(self, world: WorkerWorld) -> None:
        assembly, _ = await world.build()
        await assembly.worker.run_session()
        events = await rows(
            world, Collection.SYSTEM_EVENTS, type="session_state", account_id=world.account_id
        )
        path = [e["to"] for e in sorted(events, key=lambda e: e["ts"])]
        assert path[:6] == [
            "AUTHENTICATING",
            "RECOVERING",
            "CONNECTING",
            "READY",
            "TRADING",
            "SQUARING_OFF",
        ]
        assert path[-3:] == ["RECONCILING", "REPORTING", "SHUTTING_DOWN"]
        runs = await rows(world, Collection.RECONCILIATION_RUNS, ts={"$gte": at(0, 0)})
        triggers = {r["trigger"] for r in runs}
        assert {"STARTUP", "EOD"} <= triggers and {r["status"] for r in runs} <= {"CLEAN", "HEALED"}

    async def test_the_end_of_day_snapshot_reconstructs_from_the_fills(
        self, world: WorkerWorld
    ) -> None:
        from emporos.persistence.repositories import ExecutionRepository
        from emporos.portfolio.ledger import PositionCalculator
        from emporos.portfolio.replay import PositionReplay
        from emporos.portfolio.snapshots import SnapshotVerifier

        assembly, _ = await world.build()
        report = await assembly.worker.run_session()
        snapshot = report.close_out.snapshot if report.close_out else None
        assert snapshot is not None and snapshot.kind == "EOD"
        executions = await ExecutionRepository(world.mongo.database()).for_account(world.account_id)
        assert (
            SnapshotVerifier(PositionReplay(PositionCalculator())).problems(snapshot, executions)
            == []
        )
        assert snapshot.trades == 2 and snapshot.realised_pnl is not None
        assert snapshot.realised_pnl < Money.zero()  # bought at 100.05, sold at 99.95, plus charges
        intraday = await rows(
            world, Collection.PORTFOLIO_SNAPSHOTS, account_id=world.account_id, kind="INTRADAY"
        )
        assert intraday  # periodic snapshots were taken during the session


class TestARestart:
    async def test_a_process_dying_mid_session_never_duplicates_an_order(
        self, world: WorkerWorld
    ) -> None:
        first, tape = await world.build()
        tape.crash_at = at(9, 41)  # after the entry has filled, before square-off
        with pytest.raises(Crash):
            await first.worker.run_session()
        assert len(await rows(world, Collection.ORDERS, account_id=world.account_id)) == 1

        second, _ = await world.build(tape)  # a brand-new process over the same database
        report = await second.worker.run_session()

        assert report.final_state is SessionState.SHUTTING_DOWN and report.flat_at_close is True
        assert report.recovery is not None and report.recovery.clean
        ours = await rows(world, Collection.ORDERS, account_id=world.account_id)
        theirs = await rows(world, Collection.PAPER_ORDERS, account_id=world.account_id)
        assert sorted(o["state"] for o in ours) == ["FILLED", "FILLED"]  # the entry, then the exit
        assert sorted(o["ordertag"] for o in ours) == sorted(o["ordertag"] for o in theirs)
        (position,) = await rows(world, Collection.POSITIONS, account_id=world.account_id)
        assert position["net_quantity"] == 0


class TestFaults:
    async def test_a_discrepancy_found_mid_session_halts_trading(self, world: WorkerWorld) -> None:
        assembly, tape = await world.build()

        async def corrupt() -> None:
            db = world.mongo.database()
            await db[Collection.POSITIONS].update_one(
                {"account_id": world.account_id}, {"$set": {"net_quantity": 999}}
            )

        tape.hooks.append((at(9, 41), corrupt))
        report = await assembly.worker.run_session()

        assert world.sentinel_path.exists()  # the kill switch was engaged by the reconciler
        assert report.square_off is None  # a halted session does not trade, not even to flatten
        assert report.final_state is SessionState.SHUTTING_DOWN
        orders = await rows(world, Collection.ORDERS, account_id=world.account_id)
        assert [o["side"] for o in orders] == ["BUY"]
        events = await rows(
            world, Collection.SYSTEM_EVENTS, type="session_state", account_id=world.account_id
        )
        assert "HALTED" in {e["to"] for e in events}

    async def test_a_dirty_start_halts_before_any_strategy_runs_and_an_operator_resume_recovers(
        self, world: WorkerWorld
    ) -> None:
        db = world.mongo.database()
        await PositionsFor(world).plant()  # a position our fills do not explain
        assembly, tape = await world.build()

        async def operator_fixes_and_resumes() -> None:
            await db[Collection.POSITIONS].delete_many({"account_id": world.account_id})
            await assembly.control.release("operator")

        tape.hooks.append((at(9, 40), operator_fixes_and_resumes))
        report = await assembly.worker.run_session()

        events = sorted(
            await rows(
                world, Collection.SYSTEM_EVENTS, type="session_state", account_id=world.account_id
            ),
            key=lambda e: e["ts"],
        )
        path = [e["to"] for e in events]
        assert path[:4] == ["AUTHENTICATING", "RECOVERING", "HALTED", "TRADING"]
        assert report.traded and report.flat_at_close is True
        assert not world.sentinel_path.exists()


class PositionsFor:
    def __init__(self, world: WorkerWorld) -> None:
        self._world = world

    async def plant(self) -> None:
        record = PositionRecord(
            _id=f"{self._world.account_id}:NSE:3045", account_id=self._world.account_id,
            instrument_id="NSE:3045", net_quantity=5, average_price=Money.of("100"),
            realised_pnl=Money.zero(), updated_at=at(9, 0),
        )  # fmt: skip
        await self._world.mongo.database()[Collection.POSITIONS].insert_one(record.to_document())
        assert DAY


class TestTelemetry:
    async def test_a_running_worker_reports_its_health_and_metrics(
        self, world: WorkerWorld
    ) -> None:
        assembly, tape = await world.build()
        seen: dict[str, object] = {}

        async def look() -> None:
            seen["health"] = await assembly.telemetry.report()
            seen["metrics"] = await assembly.telemetry.sample()

        tape.hooks.append((at(9, 41), look))
        await assembly.worker.run_session()
        health, metrics = seen["health"], seen["metrics"]
        assert health.session_state == "TRADING" and health.healthy  # type: ignore[attr-defined]
        assert health.open_orders == 0 and health.kill_switch_halted is False  # type: ignore[attr-defined]
        assert metrics["WorkerHeartbeat"] == 1 and metrics["UnknownOrders"] == 0  # type: ignore[index]
        assert metrics["TicksPerMinute"] >= 1 and metrics["ReconciliationMismatches"] == 0  # type: ignore[index,operator]
        assert metrics["MaxDataStalenessSeconds"] is not None  # type: ignore[index]
