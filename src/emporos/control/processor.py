"""The worker-side command processor: turns recorded commands into effects, exactly once.

    PENDING ──claim (atomic)──▶ ACCEPTED ──▶ EXECUTING ──▶ DONE | FAILED | REJECTED
       └── not started before it expires ──▶ EXPIRED

* Exactly once. The claim is a single atomic status change in the database, so two processors (or
  a double poll) cannot both win a command. A command found ACCEPTED/EXECUTING after a restart was
  interrupted; it is re-run — safe because every handler is idempotent — up to a small attempt
  limit, after which it is FAILED, never left hanging.
* Never silently lost. A command that waited past its expiry is marked EXPIRED with a reason, and
  every status change is written to `command_results`, so the operator sees what happened.
* Never optimistic. Nothing marks a command DONE except a handler that reported it done.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from emporos.control.commands import CommandStatus, CommandType, Params, parse_command
from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.persistence.records import CommandRecord, CommandResultRecord

MAX_ATTEMPTS = 3
_INTERRUPTED = (CommandStatus.ACCEPTED.value, CommandStatus.EXECUTING.value)


class CommandStore(Protocol):
    async def in_statuses(self, statuses: Sequence[str]) -> list[CommandRecord]: ...

    async def transition(
        self,
        command_id: str,
        allowed_from: Sequence[str],
        to: str,
        at: datetime,
        *,
        reason: str = "",
        count_attempt: bool = False,
    ) -> CommandRecord | None: ...


class ResultLog(Protocol):
    async def insert(self, record: CommandResultRecord) -> None: ...


@dataclass(frozen=True)
class Outcome:
    """What a handler reports. Only DONE, REJECTED and FAILED are outcomes."""

    status: CommandStatus
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def done(cls, message: str = "", **data: Any) -> Outcome:
        return cls(CommandStatus.DONE, message, data)

    @classmethod
    def rejected(cls, reason: str, **data: Any) -> Outcome:
        return cls(CommandStatus.REJECTED, reason, data)

    @classmethod
    def failed(cls, reason: str, **data: Any) -> Outcome:
        return cls(CommandStatus.FAILED, reason, data)


class CommandHandler(Protocol):
    async def handle(self, params: Params, command: CommandRecord) -> Outcome: ...


class Wake(Protocol):
    async def wait(self, timeout: float) -> None:
        """Return when something may have changed, or after `timeout` seconds, whichever first."""
        ...


class CommandProcessor:
    def __init__(
        self,
        store: CommandStore,
        results: ResultLog,
        handlers: Mapping[CommandType, CommandHandler],
        clock: Clock,
        ids: IdGenerator,
        alerts: AlertSink,
    ) -> None:
        self._store = store
        self._results = results
        self._handlers = handlers
        self._clock = clock
        self._ids = ids
        self._alerts = alerts

    async def recover(self) -> int:
        """Finish what a previous process began. Returns how many commands were resumed."""
        resumed = 0
        for command in await self._store.in_statuses(_INTERRUPTED):
            if command.attempts >= MAX_ATTEMPTS:
                await self._finish(
                    command.id, _INTERRUPTED, Outcome.failed(
                        f"abandoned after {command.attempts} attempts across restarts"
                    )
                )  # fmt: skip
                continue
            await self._log(command.id, CommandStatus.ACCEPTED, "resumed after a restart")
            await self._execute(command.id, _INTERRUPTED)
            resumed += 1
        return resumed

    async def run_once(self) -> int:
        """Process every pending command, oldest first. Returns how many were handled."""
        handled = 0
        for command in await self._store.in_statuses((CommandStatus.PENDING.value,)):
            now = self._clock.now()
            if command.expires_at is not None and command.expires_at <= now:
                expired = await self._store.transition(
                    command.id, (CommandStatus.PENDING.value,), CommandStatus.EXPIRED.value, now,
                    reason="not started before it expired",
                )  # fmt: skip
                if expired is not None:
                    await self._log(command.id, CommandStatus.EXPIRED, expired.reason)
                    handled += 1
                continue
            claimed = await self._store.transition(
                command.id, (CommandStatus.PENDING.value,), CommandStatus.ACCEPTED.value, now
            )
            if claimed is None:
                continue  # another processor won it
            await self._log(command.id, CommandStatus.ACCEPTED, "")
            await self._execute(command.id, (CommandStatus.ACCEPTED.value,))
            handled += 1
        return handled

    async def run(self, wake: Wake, stop: asyncio.Event, poll_seconds: float = 1.0) -> None:
        """Recover, then process commands until `stop` is set. Latency is at most `poll_seconds`
        even when whatever feeds `wake` (a change stream) is down."""
        await self.recover()
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception as error:  # the store may be down: keep the loop alive
                self._alerts.raise_alert("command_processor_error", repr(error))
            with contextlib.suppress(Exception):
                await wake.wait(poll_seconds)

    # --- one command -----------------------------------------------------------------------
    async def _execute(self, command_id: str, allowed_from: Sequence[str]) -> None:
        now = self._clock.now()
        running = await self._store.transition(
            command_id, allowed_from, CommandStatus.EXECUTING.value, now, count_attempt=True
        )
        if running is None:
            return
        await self._log(command_id, CommandStatus.EXECUTING, "")
        try:
            kind, params = parse_command(running.type, running.params)
        except ValueError as error:  # the door validated it, but the schema may have moved on
            await self._finish(
                command_id, (CommandStatus.EXECUTING.value,), Outcome.rejected(str(error))
            )
            return
        handler = self._handlers.get(kind)
        if handler is None:
            await self._finish(
                command_id, (CommandStatus.EXECUTING.value,),
                Outcome.rejected(f"no handler for {kind.value} in this deployment"),
            )  # fmt: skip
            return
        try:
            outcome = await handler.handle(params, running)
        except Exception as error:
            self._alerts.raise_alert("command_failed", f"{kind.value} {command_id}: {error!r}")
            outcome = Outcome.failed(f"{type(error).__name__}: {error}")
        await self._finish(command_id, (CommandStatus.EXECUTING.value,), outcome)

    async def _finish(self, command_id: str, allowed_from: Sequence[str], outcome: Outcome) -> None:
        if not outcome.status.terminal or outcome.status is CommandStatus.EXPIRED:
            outcome = Outcome.failed(f"handler reported a non-final status {outcome.status.value}")
        await self._store.transition(
            command_id,
            allowed_from,
            outcome.status.value,
            self._clock.now(),
            reason=outcome.message,
        )
        await self._log(command_id, outcome.status, outcome.message, dict(outcome.data))

    async def _log(
        self,
        command_id: str,
        status: CommandStatus,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._results.insert(
            CommandResultRecord(
                _id=self._ids.new_ulid(),
                command_id=command_id,
                created_at=self._clock.now(),
                status=status.value,
                message=message,
                data=data or {},
            )
        )
