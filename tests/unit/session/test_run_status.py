"""A run is live from the moment it is recorded until the worker says it stopped, and a worker that
starts closes whatever a dead one left open."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from emporos.core.clock import FixedClock
from emporos.execution.order_updates import OrderUpdateTranslator
from emporos.persistence.records import StrategyRunRecord
from emporos.session.host import ManagedRun, StrategyHost
from emporos.session.run_status import RunStatusBoard
from emporos.session.updates import OrderUpdateRouter
from tests.support.strategies import InMemoryRunStore

NOW = datetime(2026, 9, 21, 4, 0, tzinfo=UTC)


def run_record(run_id: str, stopped_at: datetime | None = None) -> StrategyRunRecord:
    return StrategyRunRecord(
        _id=run_id,
        strategy_id="s1",
        session_date="2026-09-21",
        created_at=NOW - timedelta(hours=1),
        stopped_at=stopped_at,
    )


class TestBoard:
    async def test_a_stop_is_recorded_once_and_keeps_its_first_time(self) -> None:
        store, clock = InMemoryRunStore(), FixedClock(NOW)
        await store.insert(run_record("r1"))
        board = RunStatusBoard(store, clock)

        await board.stopped("r1")
        clock.advance(timedelta(minutes=5))
        await board.stopped("r1")

        assert store.records["r1"].stopped_at == NOW

    async def test_stopping_a_run_nobody_recorded_is_harmless(self) -> None:
        await RunStatusBoard(InMemoryRunStore(), FixedClock(NOW)).stopped("nope")

    async def test_a_new_worker_closes_only_the_runs_left_open(self) -> None:
        store = InMemoryRunStore()
        earlier = NOW - timedelta(days=1)
        await store.insert(run_record("open-1"))
        await store.insert(run_record("open-2"))
        await store.insert(run_record("done", stopped_at=earlier))

        closed = await RunStatusBoard(store, FixedClock(NOW)).close_open_runs()

        assert closed == 2
        assert store.records["open-1"].stopped_at == NOW
        assert store.records["open-2"].stopped_at == NOW
        assert store.records["done"].stopped_at == earlier  # an earlier stop is not overwritten


class Runner:
    async def start(self) -> None: ...
    async def handle(self, event: object) -> None: ...
    async def handle_order_update(self, update: object) -> None: ...
    async def end_session(self) -> None: ...
    async def shutdown(self) -> None: ...


class Events:
    def drain(self) -> list[object]:
        return []


class Orders:
    async def by_broker_order_id(self, broker_order_id: str) -> None:
        return None


class Told:
    def __init__(self) -> None:
        self.stopped_runs: list[str] = []

    async def stopped(self, run_id: str) -> None:
        self.stopped_runs.append(run_id)


def host(status: Told) -> StrategyHost:
    return StrategyHost(
        [ManagedRun("run-a", Runner(), "alpha"), ManagedRun("run-b", Runner(), "beta")],  # type: ignore[list-item]
        Events(),  # type: ignore[arg-type]
        OrderUpdateRouter(),
        Orders(),  # type: ignore[arg-type]
        OrderUpdateTranslator(),
        status=status,
    )


class TestHostTellsWhoKeepsTheRecord:
    async def test_stopping_a_strategy_records_its_run_only(self) -> None:
        told = Told()
        h = host(told)

        await h.stop_strategy("alpha")

        assert told.stopped_runs == ["run-a"]

    async def test_the_end_of_the_session_records_every_run_still_live_and_not_twice(self) -> None:
        told = Told()
        h = host(told)
        await h.stop_strategy("alpha")

        await h.end_session()
        await h.shutdown()

        assert told.stopped_runs == ["run-a", "run-b"]  # alpha once, beta at shutdown
