"""Starting a strategy run: snapshot the config, persist it, build the runner (plan.md §9).

    resolved config ──▶ StrategyRunLauncher ──▶ strategies + strategy_runs (config_snapshot, hash)
                              │
    StartedRun ──────────────▶ StrategyRunnerBuilder ──▶ PreparedRun (runner + history writer)

`emporos.strategies` cannot touch storage, so the piece that writes the run record lives here, one
layer up. A run is REPRODUCED by `load(run_id)`: it rebuilds the config from the stored snapshot
(hash-verified), never from the YAML that may have changed since.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Protocol

from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.domain.signals import SignalSink
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import StrategyRecord, StrategyRunRecord
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.context import StrategyContext
from emporos.strategies.history import BarRecorder, ClosedBarHistory
from emporos.strategies.positions import PositionView
from emporos.strategies.registry import StrategyRegistry
from emporos.strategies.runner import ClockSync, StrategyRunner
from emporos.strategies.snapshot import ConfigSnapshot, ConfigSnapshotter


class UnknownRunError(LookupError):
    """No strategy run has that id."""


class StrategyStore(Protocol):
    """What the launcher needs from `StrategyRepository`."""

    async def get_by_name(self, name: str) -> StrategyRecord | None: ...

    async def insert(self, record: StrategyRecord) -> None: ...

    async def replace(self, record: StrategyRecord) -> None: ...


class StrategyRunStore(Protocol):
    """What the launcher needs from `StrategyRunRepository`."""

    async def insert(self, record: StrategyRunRecord) -> None: ...

    async def get(self, record_id: str) -> StrategyRunRecord | None: ...


@dataclass(frozen=True)
class StartedRun:
    run_id: str
    config: ResolvedStrategyConfig
    snapshot: ConfigSnapshot
    session_date: str


class StrategyRunLauncher:
    def __init__(
        self,
        registry: StrategyRegistry,
        strategies: StrategyStore,
        runs: StrategyRunStore,
        clock: Clock,
        ids: IdGenerator,
        account_id: str,
        snapshotter: ConfigSnapshotter | None = None,
    ) -> None:
        self._registry = registry
        self._strategies = strategies
        self._runs = runs
        self._clock = clock
        self._ids = ids
        self._account_id = account_id
        self._snapshotter = snapshotter or ConfigSnapshotter()

    async def start(self, config: ResolvedStrategyConfig, session_date: str) -> StartedRun:
        """Begin a run: freeze the config, then record it BEFORE any market data is seen."""
        snapshot = self._snapshotter.take(config)
        strategy_id = await self._strategy_id(config.name, snapshot)
        run_id = self._ids.new_ulid()
        await self._runs.insert(
            StrategyRunRecord(
                _id=run_id,
                strategy_id=strategy_id,
                session_date=session_date,
                created_at=self._clock.now(),
                config_snapshot=dict(snapshot.document),
                config_hash=snapshot.content_hash,
                strategy_name=config.name,
                account_id=self._account_id,
            )
        )
        return StartedRun(run_id, config, snapshot, session_date)

    async def register(self, config: ResolvedStrategyConfig) -> str:
        """Put a loadable strategy in the catalogue without starting it, so it can be listed (and
        started) before it has ever run. Returns the catalogue id."""
        return await self._strategy_id(config.name, self._snapshotter.take(config))

    async def load(self, run_id: str) -> StartedRun:
        """Reproduce a recorded run from its snapshot, verifying the snapshot's hash."""
        record = await self._runs.get(run_id)
        if record is None:
            raise UnknownRunError(f"no strategy run {run_id}")
        config = self._snapshotter.restore(
            record.config_snapshot, self._registry, expected_hash=record.config_hash
        )
        snapshot = self._snapshotter.take(config)
        return StartedRun(record.id, config, snapshot, record.session_date)

    async def _strategy_id(self, name: str, snapshot: ConfigSnapshot) -> str:
        """The strategy's catalogue entry, holding the latest config it was started with."""
        existing = await self._strategies.get_by_name(name)
        if existing is not None:
            await self._strategies.replace(
                StrategyRecord(
                    _id=existing.id,
                    name=name,
                    config=dict(snapshot.document),
                    behaviour_hash=snapshot.behaviour_hash,
                )
            )
            return existing.id
        record = StrategyRecord(
            _id=self._ids.new_ulid(),
            name=name,
            config=dict(snapshot.document),
            behaviour_hash=snapshot.behaviour_hash,
        )
        try:
            await self._strategies.insert(record)
        except DuplicateRecordError:  # another process registered the name first
            winner = await self._strategies.get_by_name(name)
            assert winner is not None
            return winner.id
        return record.id


@dataclass(frozen=True)
class RunEnvironment:
    """Everything a run needs from the outside world. Nothing here can place an order."""

    clock: Clock
    clock_sync: ClockSync
    sink: SignalSink
    positions: PositionView
    alerts: AlertSink


@dataclass(frozen=True)
class PreparedRun:
    run_id: str
    runner: StrategyRunner
    context: StrategyContext
    history: BarRecorder  # warm-up bars may be recorded here before the runner starts


class StrategyRunnerBuilder:
    def __init__(self, registry: StrategyRegistry) -> None:
        self._registry = registry

    def build(self, started: StartedRun, environment: RunEnvironment) -> PreparedRun:
        strategy = self._registry.create(started.config)
        history = ClosedBarHistory(environment.clock)
        context = StrategyContext(
            run_id=started.run_id,
            config=started.config,
            clock=environment.clock,
            logger=logging.getLogger(f"emporos.strategy.{started.config.name}"),
            history=history,
            positions=environment.positions,
            rng=random.Random(started.snapshot.content_hash),  # same config, same draws
        )
        runner = StrategyRunner(
            strategy,
            context,
            history,
            environment.sink,
            environment.clock_sync,
            environment.alerts,
        )
        return PreparedRun(started.run_id, runner, context, history)
