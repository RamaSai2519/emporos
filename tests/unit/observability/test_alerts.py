"""An alert raised in synchronous code is durable once the database is reachable."""

import pytest

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.trading_mode import TradingMode
from emporos.observability.alerts import (
    EventOutbox,
    LifecycleEvents,
    OutboxAlertSink,
    WorkerHealthReports,
)
from emporos.persistence.records import SystemEventRecord
from emporos.session.lifecycle import SessionLifecycle, SessionState
from tests.support.records import NOW


class Store:
    def __init__(self, fail_after: int | None = None) -> None:
        self.records: list[SystemEventRecord] = []
        self.fail_after = fail_after

    async def insert(self, record: SystemEventRecord) -> None:
        if self.fail_after is not None and len(self.records) >= self.fail_after:
            raise OSError("database down")
        self.records.append(record)


async def test_an_alert_becomes_a_system_event_with_its_name_and_message() -> None:
    outbox = EventOutbox()
    sink = OutboxAlertSink(outbox, FixedClock(NOW), IdGenerator())
    sink.raise_alert("stale_feed", "no ticks for 30s")
    store = Store()
    assert outbox.pending == 1 and await outbox.drain(store) == 1 and outbox.pending == 0
    (event,) = store.records
    assert (event.type, event.ts) == ("alert", NOW)
    assert event.model_dump()["name"] == "stale_feed"
    assert event.model_dump()["message"] == "no ticks for 30s"


async def test_events_the_database_could_not_take_stay_queued_in_order() -> None:
    outbox = EventOutbox()
    sink = OutboxAlertSink(outbox, FixedClock(NOW), IdGenerator())
    for n in range(3):
        sink.raise_alert(f"a{n}", "")
    store = Store(fail_after=1)
    with pytest.raises(OSError):
        await outbox.drain(store)
    assert outbox.pending == 2  # the first is durable; the other two wait
    store.fail_after = None
    assert await outbox.drain(store) == 2
    assert [r.model_dump()["name"] for r in store.records] == ["a0", "a1", "a2"]


def test_a_full_outbox_drops_the_oldest_rather_than_growing_without_bound() -> None:
    outbox = EventOutbox(max_queued=2)
    sink = OutboxAlertSink(outbox, FixedClock(NOW), IdGenerator())
    for n in range(5):
        sink.raise_alert(f"a{n}", "")
    assert outbox.pending == 2


async def test_every_session_state_change_is_recorded() -> None:
    outbox = EventOutbox()
    lifecycle = SessionLifecycle(
        FixedClock(NOW), (LifecycleEvents(outbox, IdGenerator(), "acct-1"),)
    )
    lifecycle.transition(SessionState.AUTHENTICATING, "go")
    store = Store()
    await outbox.drain(store)
    doc = store.records[0].model_dump()
    assert (doc["type"], doc["from"], doc["to"], doc["reason"], doc["account_id"]) == (
        "session_state", "STARTING", "AUTHENTICATING", "go", "acct-1",
    )  # fmt: skip


class TestWorkerHealthReports:
    async def test_reports_trading_mode_and_current_health(self) -> None:
        outbox = EventOutbox()
        healthy = {"broker": True, "feed": False}
        reporter = WorkerHealthReports(
            outbox, IdGenerator(), "acct-1", FixedClock(NOW),
            TradingMode.LIVE, lambda: healthy["broker"], lambda: healthy["feed"],
        )  # fmt: skip

        await reporter.report()

        store = Store()
        await outbox.drain(store)
        doc = store.records[0].model_dump()
        assert (doc["type"], doc["account_id"], doc["ts"]) == ("worker_health", "acct-1", NOW)
        assert (doc["trading_mode"], doc["broker_healthy"], doc["feed_healthy"]) == (
            "live", True, False,
        )  # fmt: skip

    async def test_reads_health_fresh_on_every_call_not_once_at_construction(self) -> None:
        outbox = EventOutbox()
        healthy = {"broker": False, "feed": False}
        reporter = WorkerHealthReports(
            outbox, IdGenerator(), "acct-1", FixedClock(NOW),
            TradingMode.PAPER, lambda: healthy["broker"], lambda: healthy["feed"],
        )  # fmt: skip

        healthy["broker"] = True
        await reporter.report()

        store = Store()
        await outbox.drain(store)
        assert store.records[0].model_dump()["broker_healthy"] is True
