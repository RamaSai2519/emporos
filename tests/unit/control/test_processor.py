"""A command is executed exactly once, never silently lost, and never falsely reported done."""

import asyncio
from datetime import timedelta

import pytest

from emporos.control.commands import CommandStatus, CommandType, Params
from emporos.control.processor import MAX_ATTEMPTS, CommandProcessor, Outcome
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.persistence.records import CommandRecord, CommandResultRecord
from tests.support.fakes import RecordingAlertSink
from tests.support.records import NOW


class Store:
    """The atomic single-winner claim of `CommandRepository.transition`, in memory."""

    def __init__(self, commands: list[CommandRecord]) -> None:
        self.rows = {c.id: c for c in commands}

    async def in_statuses(self, statuses):  # type: ignore[no-untyped-def]
        return sorted(
            (c for c in self.rows.values() if c.status in statuses), key=lambda c: c.created_at
        )

    async def transition(self, command_id, allowed_from, to, at, *, reason="", count_attempt=False):  # type: ignore[no-untyped-def]
        current = self.rows[command_id]
        if current.status not in allowed_from:
            return None
        await asyncio.sleep(0)  # yield: lets a racing processor interleave
        current = self.rows[command_id]
        if current.status not in allowed_from:
            return None
        self.rows[command_id] = current.model_copy(
            update={
                "status": to, "updated_at": at, "reason": reason,
                "attempts": current.attempts + (1 if count_attempt else 0),
            }
        )  # fmt: skip
        return self.rows[command_id]


class Results:
    def __init__(self) -> None:
        self.rows: list[CommandResultRecord] = []

    async def insert(self, record: CommandResultRecord) -> None:
        self.rows.append(record)


class Handler:
    def __init__(self, outcome: Outcome | Exception | None = None) -> None:
        self.calls: list[str] = []
        self.outcome = outcome or Outcome.done("ok")

    async def handle(self, params: Params, command: CommandRecord) -> Outcome:
        self.calls.append(command.id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def command(
    n: int = 1, status: str = "PENDING", kind: str = "RECONCILE_NOW", **kw: object
) -> CommandRecord:
    fields: dict[str, object] = {
        "_id": f"c{n}", "idempotency_key": f"k{n}", "type": kind, "status": status,
        "created_at": NOW + timedelta(seconds=n), "params": {},
        "expires_at": NOW + timedelta(minutes=10),
    }  # fmt: skip
    return CommandRecord(**{**fields, **kw})  # type: ignore[arg-type]


class Rig:
    def __init__(self, commands: list[CommandRecord], handler: Handler | None = None) -> None:
        self.store, self.results, self.alerts = Store(commands), Results(), RecordingAlertSink()
        self.clock = FixedClock(NOW)
        self.handler = handler or Handler()
        self.processor = CommandProcessor(
            self.store, self.results, {CommandType.RECONCILE_NOW: self.handler},  # type: ignore[dict-item]
            self.clock, IdGenerator(), self.alerts,
        )  # fmt: skip

    def status(self, n: int = 1) -> str:
        return self.store.rows[f"c{n}"].status

    def log(self, n: int = 1) -> list[str | None]:
        return [r.status for r in self.results.rows if r.command_id == f"c{n}"]


class TestExecution:
    async def test_a_command_walks_pending_accepted_executing_done_with_every_step_logged(
        self,
    ) -> None:
        rig = Rig([command()])
        assert await rig.processor.run_once() == 1
        assert rig.status() == "DONE" and rig.handler.calls == ["c1"]
        assert rig.log() == ["ACCEPTED", "EXECUTING", "DONE"]
        assert rig.store.rows["c1"].attempts == 1 and rig.store.rows["c1"].reason == "ok"

    async def test_commands_run_oldest_first_and_only_once(self) -> None:
        rig = Rig([command(2), command(1)])
        await rig.processor.run_once()
        await rig.processor.run_once()
        assert rig.handler.calls == ["c1", "c2"]

    async def test_two_processors_racing_for_the_same_command_execute_it_once(self) -> None:
        rig = Rig([command()])
        other = CommandProcessor(
            rig.store, rig.results, {CommandType.RECONCILE_NOW: rig.handler},  # type: ignore[dict-item]
            rig.clock, IdGenerator(), rig.alerts,
        )  # fmt: skip
        await asyncio.gather(rig.processor.run_once(), other.run_once())
        assert rig.handler.calls == ["c1"] and rig.status() == "DONE"

    async def test_a_handler_that_raises_fails_the_command_and_alerts(self) -> None:
        rig = Rig([command()], Handler(RuntimeError("boom")))
        await rig.processor.run_once()
        assert rig.status() == "FAILED" and "boom" in rig.store.rows["c1"].reason
        assert [n for n, _ in rig.alerts.alerts] == ["command_failed"]

    async def test_a_rejection_is_recorded_with_its_reason(self) -> None:
        rig = Rig([command()], Handler(Outcome.rejected("nope")))
        await rig.processor.run_once()
        assert (rig.status(), rig.store.rows["c1"].reason) == ("REJECTED", "nope")

    async def test_a_handler_cannot_report_a_non_final_status(self) -> None:
        rig = Rig([command()], Handler(Outcome(CommandStatus.EXECUTING, "still going")))
        await rig.processor.run_once()
        assert rig.status() == "FAILED"

    async def test_a_command_with_no_handler_is_rejected_not_dropped(self) -> None:
        rig = Rig([command(kind="CANCEL_ORDER", params={"order_id": "o"})])
        await rig.processor.run_once()
        assert rig.status() == "REJECTED" and "no handler" in rig.store.rows["c1"].reason

    async def test_parameters_that_no_longer_validate_are_rejected(self) -> None:
        rig = Rig([command(params={"surprise": 1})])
        await rig.processor.run_once()
        assert rig.status() == "REJECTED" and rig.handler.calls == []


class TestExpiry:
    async def test_a_command_that_waited_too_long_expires_with_a_reason_instead_of_vanishing(
        self,
    ) -> None:
        rig = Rig([command(expires_at=NOW - timedelta(seconds=1))])
        await rig.processor.run_once()
        assert rig.status() == "EXPIRED" and rig.handler.calls == []
        assert "expired" in rig.store.rows["c1"].reason and rig.log() == ["EXPIRED"]

    async def test_a_command_issued_while_the_worker_was_down_runs_on_recovery_if_still_fresh(
        self,
    ) -> None:
        rig = Rig([command(), command(2, expires_at=NOW - timedelta(minutes=1))])
        await rig.processor.run_once()
        assert (rig.status(1), rig.status(2)) == ("DONE", "EXPIRED")


class TestRestart:
    @pytest.mark.parametrize("status", ["ACCEPTED", "EXECUTING"])
    async def test_a_command_interrupted_by_a_restart_is_re_run(self, status: str) -> None:
        rig = Rig([command(status=status, attempts=1)])
        assert await rig.processor.recover() == 1
        assert rig.status() == "DONE" and rig.handler.calls == ["c1"]
        assert rig.store.rows["c1"].attempts == 2
        assert (
            rig.log()[0] == "ACCEPTED" and rig.results.rows[0].message == "resumed after a restart"
        )

    async def test_a_command_that_keeps_killing_the_worker_is_abandoned_not_retried_forever(
        self,
    ) -> None:
        rig = Rig([command(status="EXECUTING", attempts=MAX_ATTEMPTS)])
        assert await rig.processor.recover() == 0
        assert rig.status() == "FAILED" and "abandoned" in rig.store.rows["c1"].reason
        assert rig.handler.calls == []

    async def test_a_finished_command_is_never_touched_by_recovery(self) -> None:
        rig = Rig([command(status="DONE")])
        assert await rig.processor.recover() == 0 and rig.handler.calls == []


class TestRunLoop:
    async def test_the_loop_recovers_then_processes_until_stopped_and_survives_a_store_error(
        self,
    ) -> None:
        rig = Rig([command(status="EXECUTING", attempts=1)])
        stop, waits = asyncio.Event(), []

        class Wake:
            async def wait(self, timeout: float) -> None:
                waits.append(timeout)
                if len(waits) == 1:
                    rig.store.rows["c2"] = command(2)
                else:
                    stop.set()

        original = rig.store.in_statuses
        failures = {"left": 1}

        async def flaky(statuses):  # type: ignore[no-untyped-def]
            if failures["left"] and statuses == ("PENDING",):
                failures["left"] -= 1
                raise OSError("db down")
            return await original(statuses)

        rig.store.in_statuses = flaky  # type: ignore[method-assign]
        await rig.processor.run(Wake(), stop, poll_seconds=1.0)
        assert rig.status(1) == "DONE" and rig.status(2) == "DONE"
        assert [n for n, _ in rig.alerts.alerts] == ["command_processor_error"]
        assert waits[0] == 1.0
