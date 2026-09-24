"""The kill switch (plan.md §11, NSE algo framework §1.1): a way to stop all new orders that does
not depend on the dashboard, the API, or even the database.

    operator ── `emporos halt` ──▶ KillSwitchControl ──▶ Mongo `kill_switch` document
                                                    └──▶ file sentinel on the worker's disk
    worker:  KillSwitchMonitor ── polls both every 5 s ──▶ KillSwitchReading ──▶ KillSwitchGuard

Reading rules (all fail safe):

* halted if ANY source says halted, whatever the others say;
* a source that cannot be read (Mongo down, unreadable file, a read that hangs) makes the state
  UNKNOWN, and unknown is treated as halted — an operator's halt in the database can never be
  missed because the database was briefly unreachable;
* a reading older than `max_age` (a wedged monitor loop) is also unknown.

The file sentinel is what keeps the switch working with MongoDB down.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from emporos.core.clock import Clock, Sleeper
from emporos.core.errors import ConfigurationError
from emporos.persistence.records import KillSwitchRecord
from emporos.persistence.repositories import KillSwitchRepository
from emporos.risk.snapshot import KillSwitchReading

DEFAULT_POLL_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 2.0
_STALE_AFTER_POLLS = 3

# A halt set by one of these is a protective stop after an anomaly (EM-189): it blocks NEW orders
# but lets exits through, because being unable to leave a position is worse than the anomaly. An
# operator's halt, or the reconciler's, still blocks everything.
TRIPWIRE_SETTER = "tripwire"
EXIT_PERMITTING_SETTERS = frozenset({TRIPWIRE_SETTER})
_SET_BY = re.compile(r"^set by (\S+) at ", re.MULTILINE)


@dataclass(frozen=True)
class SourceReading:
    halted: bool
    reason: str = ""
    exits_permitted: bool = False  # only meaningful when halted


class KillSwitchReader(Protocol):
    """One place the switch can be read from. Raises if it cannot be read."""

    name: str

    async def read(self) -> SourceReading: ...


class KillSwitchWriter(Protocol):
    name: str

    async def engage(self, reason: str, set_by: str, at: datetime) -> None: ...

    async def release(self, set_by: str, at: datetime) -> None: ...


class FileSentinelKillSwitch:
    """Halted while the file exists. Its text is the reason. Needs nothing but a disk."""

    name = "file"

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    async def read(self) -> SourceReading:
        try:
            reason = self._path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return SourceReading(False)
        # The writer's own footer is the LAST such line: a reason cannot forge it.
        setters = _SET_BY.findall(reason)
        return SourceReading(True, reason, bool(setters) and setters[-1] in EXIT_PERMITTING_SETTERS)

    async def engage(self, reason: str, set_by: str, at: datetime) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = f"{reason}\nset by {set_by} at {at.isoformat()}\n"
        # Write-then-rename: a reader never sees a half-written sentinel.
        fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=".halt-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temp, self._path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)
            raise

    async def release(self, set_by: str, at: datetime) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()


class MongoKillSwitch:
    """The `kill_switch` document. A missing document means never halted."""

    name = "mongo"

    def __init__(self, repository: KillSwitchRepository) -> None:
        self._repository = repository

    async def read(self) -> SourceReading:
        record = await self._repository.current()
        if record is None or not record.halted:
            return SourceReading(False)
        return SourceReading(True, record.reason, record.set_by in EXIT_PERMITTING_SETTERS)

    async def engage(self, reason: str, set_by: str, at: datetime) -> None:
        await self._repository.save(self._record(True, reason, set_by, at))

    async def release(self, set_by: str, at: datetime) -> None:
        await self._repository.save(self._record(False, "", set_by, at))

    @staticmethod
    def _record(halted: bool, reason: str, set_by: str, at: datetime) -> KillSwitchRecord:
        return KillSwitchRecord(
            _id=KillSwitchRepository.CURRENT_ID,
            halted=halted,
            reason=reason,
            set_by=set_by,
            changed_at=at,
        )


class KillSwitchMonitor:
    """Polls every source, concurrently, and holds the latest combined reading.

    `reading()` is synchronous and cheap, so a risk rule can ask on every signal. A halt is seen
    at the next poll at the latest: within `poll_seconds` of it being set."""

    def __init__(
        self,
        sources: Sequence[KillSwitchReader],
        clock: Clock,
        sleeper: Sleeper,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
    ) -> None:
        if not sources:
            raise ConfigurationError("a kill switch with no source could never be set")
        if poll_seconds <= 0 or read_timeout_seconds <= 0:
            raise ConfigurationError("kill switch intervals must be positive")
        self._sources = tuple(sources)
        self._clock = clock
        self._sleeper = sleeper
        self._poll = poll_seconds
        self._read_timeout = read_timeout_seconds
        self._max_age = timedelta(seconds=poll_seconds * _STALE_AFTER_POLLS)
        self._latest: tuple[datetime, KillSwitchReading] | None = None
        self._stopping = False

    def reading(self) -> KillSwitchReading:
        if self._latest is None:
            return KillSwitchReading(reason="the kill switch has not been read yet")
        taken_at, reading = self._latest
        age = self._clock.now() - taken_at
        if age > self._max_age:
            return KillSwitchReading(
                source="monitor",
                reason=f"the kill switch was last read {age.total_seconds():.0f}s ago",
            )
        return reading

    async def refresh(self) -> KillSwitchReading:
        results = await asyncio.gather(
            *(self._read(source) for source in self._sources), return_exceptions=True
        )
        reading = self._combine(results)
        self._latest = (self._clock.now(), reading)
        return reading

    async def run(self) -> None:
        """Poll until `stop()`. A failed poll never ends the loop: the reading just goes unknown."""
        while not self._stopping:
            with contextlib.suppress(Exception):
                await self.refresh()
            await self._sleeper.sleep(self._poll)

    def stop(self) -> None:
        self._stopping = True

    async def _read(self, source: KillSwitchReader) -> SourceReading:
        return await asyncio.wait_for(source.read(), timeout=self._read_timeout)

    def _combine(self, results: Sequence[SourceReading | BaseException]) -> KillSwitchReading:
        halted = [
            (src.name, r) for src, r in zip(self._sources, results, strict=True)
            if isinstance(r, SourceReading) and r.halted
        ]  # fmt: skip
        if halted:
            names = ",".join(name for name, _ in halted)
            reasons = "; ".join(r.reason for _, r in halted if r.reason)
            return KillSwitchReading(
                halted=True, known=True, source=names, reason=reasons,
                exits_permitted=all(r.exits_permitted for _, r in halted),
            )  # fmt: skip
        failed = [
            src.name for src, r in zip(self._sources, results, strict=True)
            if isinstance(r, BaseException)
        ]  # fmt: skip
        if failed:
            names = ",".join(failed)
            return KillSwitchReading(
                halted=True, known=False, source=names, reason=f"could not read: {names}"
            )
        return KillSwitchReading(halted=False, known=True, source="all")


@dataclass(frozen=True)
class SinkOutcome:
    """What happened when one place was written or read."""

    name: str
    ok: bool
    halted: bool | None = None
    detail: str = ""


class KillSwitchControl:
    """What `emporos halt` uses: set, clear and inspect every place at once, with no other
    subsystem running. It reports each place separately, because 'the sentinel landed but Mongo
    did not' is exactly what an operator in an incident needs to see."""

    def __init__(
        self,
        writers: Sequence[KillSwitchWriter],
        readers: Sequence[KillSwitchReader],
        clock: Clock,
        timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
    ) -> None:
        if not writers:
            raise ConfigurationError("a kill switch with no place to write could never be set")
        self._writers = tuple(writers)
        self._readers = tuple(readers)
        self._clock = clock
        self._timeout = timeout_seconds

    async def engage(self, reason: str, set_by: str) -> list[SinkOutcome]:
        if not reason.strip():
            raise ValueError("a halt must say why")
        at = self._clock.now()
        return await self._write(lambda w: w.engage(reason, set_by, at))

    async def release(self, set_by: str) -> list[SinkOutcome]:
        at = self._clock.now()
        return await self._write(lambda w: w.release(set_by, at))

    async def status(self) -> list[SinkOutcome]:
        async def one(reader: KillSwitchReader) -> SinkOutcome:
            try:
                got = await asyncio.wait_for(reader.read(), timeout=self._timeout)
            except Exception as error:
                return SinkOutcome(reader.name, False, None, _describe(error))
            return SinkOutcome(reader.name, True, got.halted, got.reason)

        return list(await asyncio.gather(*(one(r) for r in self._readers)))

    async def _write(
        self, action: Callable[[KillSwitchWriter], Awaitable[None]]
    ) -> list[SinkOutcome]:
        async def one(writer: KillSwitchWriter) -> SinkOutcome:
            try:
                await asyncio.wait_for(action(writer), timeout=self._timeout)
            except Exception as error:
                return SinkOutcome(writer.name, False, None, _describe(error))
            return SinkOutcome(writer.name, True)

        return list(await asyncio.gather(*(one(w) for w in self._writers)))


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
