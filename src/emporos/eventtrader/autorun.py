"""Dev without anyone watching (EM-240): an ordered list of steps, run to the first one that cannot
finish yet, over a state file, so a timer can fire it every half hour and it picks up where the
last firing stopped.

* a step is DONE (recorded, never repeated), WAITing for something outside (the snapshot's inputs
  are not ready: the next firing tries again), INCOMPLETE (its calls mostly failed: the next firing
  retries it, a few times, and never skips past it) or FAILED (the whole run halts with the reason
  and stays halted until the operator removes the halt from the state file);
* an exception is a failure, never a skip;
* one firing at a time (a lock file), and a line per event in a status file anyone can read.

Nothing here knows what a step does: the steps are built by the composition root."""

from __future__ import annotations

import fcntl
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import IO, Any, Protocol

from emporos.core.clock import Clock

__all__ = [
    "Autorun",
    "Outcome",
    "RunState",
    "StatusFile",
    "Step",
    "StepResult",
    "already_running",
]

MAX_INCOMPLETE_ATTEMPTS = 3


class Outcome(StrEnum):
    DONE = "done"
    WAIT = "wait"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


@dataclass(frozen=True)
class StepResult:
    outcome: Outcome
    detail: str = ""


class Step(Protocol):
    name: str

    def run(self) -> StepResult: ...


class RunState:
    """What has been done, what has been retried, and whether the run is halted."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = {"done": {}, "attempts": {}, "halted": None}
        if path.exists():
            self._data.update(json.loads(path.read_text(encoding="utf-8")))

    @property
    def halted(self) -> str | None:
        reason = self._data["halted"]
        return None if reason is None else str(reason)

    def is_done(self, step: str) -> bool:
        return step in self._data["done"]

    def mark_done(self, step: str, at: datetime, detail: str) -> None:
        self._data["done"][step] = {"at": at.isoformat(), "detail": detail}
        self._save()

    def attempts(self, step: str) -> int:
        return int(self._data["attempts"].get(step, 0))

    def add_attempt(self, step: str) -> int:
        self._data["attempts"][step] = self.attempts(step) + 1
        self._save()
        return self.attempts(step)

    def halt(self, reason: str) -> None:
        self._data["halted"] = reason
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8")
        temporary.replace(self._path)


class StatusFile:
    """Append-only, one line per event, each with its UTC time."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self._path, self._clock = path, clock

    def line(self, step: str, status: str, detail: str = "") -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("# Track L autorun status (UTC), oldest first\n", "utf-8")
        stamp = self._clock.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        text = f"- {stamp} | {step} | {status}" + (f" | {detail}" if detail else "")
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(text.replace("\n", " ") + "\n")


class _Lock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


def already_running(directory: Path) -> bool:
    """True when another firing holds the lock."""
    lock = _Lock(directory / "autorun.lock")
    if lock.acquire():
        lock.release()
        return False
    return True


class Autorun:
    def __init__(
        self,
        steps: Sequence[Step],
        directory: Path,
        clock: Clock,
        log: Callable[[str], None] = lambda _message: None,
    ) -> None:
        self._steps, self._directory, self._clock, self._log = steps, directory, clock, log
        self._state = RunState(directory / "state.json")
        self._status = StatusFile(directory / "STATUS.md", clock)

    def run_once(self) -> int:
        """0 = made progress or is waiting or finished; 1 = halted (a step failed)."""
        lock = _Lock(self._directory / "autorun.lock")
        if not lock.acquire():
            self._log("another firing is running; nothing to do")
            return 0
        try:
            return self._run()
        finally:
            lock.release()

    def _run(self) -> int:
        if self._state.halted is not None:
            self._log(f"halted: {self._state.halted}")
            return 1
        for step in self._steps:
            if self._state.is_done(step.name):
                continue
            result = self._attempt(step)
            if result.outcome is Outcome.DONE:
                self._state.mark_done(step.name, self._clock.now(), result.detail)
                self._status.line(step.name, "done", result.detail)
                continue
            return self._stop(step, result)
        self._status.line("autorun", "ALL STEPS DONE")
        return 0

    def _attempt(self, step: Step) -> StepResult:
        try:
            return step.run()
        except Exception as error:
            return StepResult(Outcome.FAILED, f"{type(error).__name__}: {error}")

    def _stop(self, step: Step, result: StepResult) -> int:
        if result.outcome is Outcome.WAIT:
            self._status.line(step.name, "waiting", result.detail)
            return 0
        if result.outcome is Outcome.INCOMPLETE:
            attempts = self._state.add_attempt(step.name)
            if attempts < MAX_INCOMPLETE_ATTEMPTS:
                self._status.line(
                    step.name, f"incomplete (attempt {attempts}, will retry)", result.detail
                )
                return 0
            result = StepResult(Outcome.FAILED, f"incomplete {attempts} times: {result.detail}")
        self._state.halt(f"{step.name}: {result.detail}")
        self._status.line(step.name, "HALTED", result.detail)
        return 1
