"""Whether a strategy run is live, written by the worker and read by the API.

A run is recorded, with its config, before any market data is seen (`StrategyRunLauncher`). This
adds the other end: the moment it stops, whether by an operator's STOP, the end of the session, or
because the worker that started it is gone. The worker is the only writer, so a run with no
`stopped_at` is live exactly while the worker that launched it is running; a fresh worker closes
whatever a previous one left open.
"""

from __future__ import annotations

from typing import Any, Protocol

from emporos.core.clock import Clock
from emporos.persistence.records import StrategyRunRecord


class RunStore(Protocol):
    async def get(self, record_id: str) -> StrategyRunRecord | None: ...

    async def find(
        self,
        query: dict[str, Any],
        *,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 0,
    ) -> list[StrategyRunRecord]: ...

    async def replace(self, record: StrategyRunRecord) -> None: ...


class RunStatus(Protocol):
    """What the host tells whoever keeps the record when a run ends."""

    async def stopped(self, run_id: str) -> None: ...


class RunStatusBoard:
    def __init__(self, runs: RunStore, clock: Clock) -> None:
        self._runs = runs
        self._clock = clock

    async def stopped(self, run_id: str) -> None:
        """Idempotent: a run that already has a stop time keeps the first one."""
        record = await self._runs.get(run_id)
        if record is not None and record.stopped_at is None:
            await self._runs.replace(record.model_copy(update={"stopped_at": self._clock.now()}))

    async def close_open_runs(self) -> int:
        """Called once as a worker starts: any run still open belongs to a worker that is gone."""
        open_runs = await self._runs.find({"stopped_at": None})
        for record in open_runs:
            await self._runs.replace(record.model_copy(update={"stopped_at": self._clock.now()}))
        return len(open_runs)
