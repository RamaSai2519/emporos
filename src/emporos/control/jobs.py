"""Long-running commands (backtests, backfills) run off the command loop and report as they go.

A job never blocks the worker's poll loop: `start` returns at once with a job id and the work runs
as an asyncio task. Its progress and result are appended to `command_results` for the operator; the
command itself is DONE when the job was STARTED, and the result log says how it ended.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.persistence.records import CommandResultRecord

JobFunction = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]


class JobUnavailableError(LookupError):
    """This deployment has no runner for that kind of job."""


class ResultLog(Protocol):
    async def insert(self, record: CommandResultRecord) -> None: ...


class BackgroundJobs:
    def __init__(
        self,
        runners: Mapping[str, JobFunction],
        results: ResultLog,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._runners = dict(runners)
        self._results = results
        self._clock = clock
        self._ids = ids
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self, kind: str, command_id: str, params: dict[str, Any]) -> str:
        runner = self._runners.get(kind)
        if runner is None:
            raise JobUnavailableError(f"no {kind} runner is configured in this deployment")
        job_id = f"{kind}-{command_id}"
        if job_id in self._tasks:
            return job_id  # the command was re-run after a restart: the job is not started twice
        await self._log(command_id, "job started", {"job_id": job_id})
        self._tasks[job_id] = asyncio.create_task(self._run(job_id, command_id, runner, params))
        return job_id

    async def wait(self, job_id: str) -> None:
        await self._tasks[job_id]

    async def _run(
        self, job_id: str, command_id: str, runner: JobFunction, params: dict[str, Any]
    ) -> None:
        try:
            result = await runner(params)
        except Exception as error:
            await self._log(
                command_id, f"job failed: {type(error).__name__}: {error}", {"job_id": job_id}
            )
            return
        await self._log(command_id, "job finished", {"job_id": job_id, **result})

    async def _log(self, command_id: str, message: str, data: dict[str, Any]) -> None:
        await self._results.insert(
            CommandResultRecord(
                _id=self._ids.new_ulid(), command_id=command_id, created_at=self._clock.now(),
                message=message, data=data,
            )
        )  # fmt: skip
