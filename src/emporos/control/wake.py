"""What makes the command processor look at the queue sooner than its next poll.

A MongoDB change stream on `commands` wakes it the moment a command is inserted. It is an
OPTIMISATION, never a dependency: the processor polls at least every second regardless, so a dropped
or unavailable stream costs latency (≤ 1 s) and nothing else. When the stream breaks it is
reported once and re-opened after a back-off.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Sleeper


class ChangeSource(Protocol):
    """Yields once per change to the watched collection; raises when the stream breaks."""

    def changes(self) -> Any: ...


class PollingWake:
    """No stream at all: just wait out the poll interval."""

    def __init__(self, sleeper: Sleeper) -> None:
        self._sleeper = sleeper

    async def wait(self, timeout: float) -> None:
        await self._sleeper.sleep(timeout)


class ChangeStreamWake:
    def __init__(
        self,
        watch: Callable[[], Awaitable[Any]],
        sleeper: Sleeper,
        alerts: AlertSink,
        retry_seconds: float = 5.0,
    ) -> None:
        self._watch = watch
        self._sleeper = sleeper
        self._alerts = alerts
        self._retry = retry_seconds
        self._event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._degraded = False

    @property
    def degraded(self) -> bool:
        return self._degraded

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._pump())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            return  # the poll interval elapsed: look anyway
        self._event.clear()

    async def _pump(self) -> None:
        while True:
            try:
                stream = await self._watch()
                async with stream:
                    self._degraded = False
                    async for _ in stream:
                        self._event.set()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not self._degraded:
                    self._degraded = True
                    self._alerts.raise_alert("command_stream_down", f"polling instead: {error!r}")
            await self._sleeper.sleep(self._retry)
