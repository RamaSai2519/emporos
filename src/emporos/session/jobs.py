"""Periodic work driven by the session's own poll loop, on the injected clock.

The worker polls; it does not spawn a task per chore. Each poll asks the scheduler which jobs are
due and runs them one after another, so ordering is total and deterministic (a fill applies before
the next thing evaluates) and a test drives a whole session in virtual time. A job that raises is
isolated: it is reported and retried at its next interval, and never stops the session or the other
jobs — except where the failure is one the operator must see, which the job reports itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock


@dataclass(frozen=True)
class Job:
    name: str
    interval: timedelta
    action: Callable[[], Awaitable[object]]

    def __post_init__(self) -> None:
        if self.interval <= timedelta(0):
            raise ValueError(f"job {self.name!r} needs a positive interval")


class JobScheduler:
    def __init__(self, jobs: list[Job], clock: Clock, alerts: AlertSink) -> None:
        names = [job.name for job in jobs]
        if len(names) != len(set(names)):
            raise ValueError("job names must be unique")
        self._jobs = jobs
        self._clock = clock
        self._alerts = alerts
        self._last: dict[str, datetime] = {}
        self._failing: set[str] = set()

    def add(self, job: Job) -> None:
        """Register one more job (the composition root adds jobs that need the finished graph)."""
        if any(existing.name == job.name for existing in self._jobs):
            raise ValueError("job names must be unique")
        self._jobs.append(job)

    async def run_due(self) -> list[str]:
        """Run every job whose interval has elapsed; return the names that ran."""
        ran: list[str] = []
        for job in self._jobs:
            now = self._clock.now()
            last = self._last.get(job.name)
            if last is not None and now - last < job.interval:
                continue
            self._last[job.name] = now
            ran.append(job.name)
            try:
                await job.action()
            except Exception as error:
                if job.name not in self._failing:  # one alert per outage, not one per interval
                    self._failing.add(job.name)
                    self._alerts.raise_alert(f"job_failed:{job.name}", repr(error))
            else:
                self._failing.discard(job.name)
        return ran

    async def run_now(self, name: str) -> None:
        """Run one job immediately, whatever its interval (used at the end of a session)."""
        job = next(j for j in self._jobs if j.name == name)
        self._last[name] = self._clock.now()
        await job.action()
