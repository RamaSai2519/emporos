"""Composition root for the control-plane API process: the only place its parts are chosen.

The API process reads MongoDB and writes `commands` — nothing else. It never builds a broker, an
execution engine or a risk engine; it does not even import them (an import contract and a
process-level test enforce that).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from emporos.api.app import ApiServices
from emporos.api.auth import Argon2Passcodes, AuthService, LoginThrottle, TokenService
from emporos.api.queries import QueryService
from emporos.api.stores import MongoPasscodeStore
from emporos.control.submitter import CommandSubmitter
from emporos.control.wake import ChangeStreamWake
from emporos.core.alerts import AlertSink
from emporos.core.clock import Clock, Sleeper
from emporos.core.ids import IdGenerator
from emporos.persistence.collections import Collection
from emporos.persistence.repositories import (
    CommandRepository,
    CommandResultRepository,
    Database,
    ExecutionRepository,
    KillSwitchRepository,
    OrderEventRepository,
    OrderRepository,
    PortfolioSnapshotRepository,
    PositionRepository,
    ReconciliationRunRepository,
    RiskEventRepository,
    SignalRepository,
    StrategyRepository,
    StrategyRunRepository,
    SystemEventRepository,
    UserRepository,
)
from emporos.risk.limits import RiskLimits

_WATCHED = (Collection.ORDERS, Collection.POSITIONS, Collection.COMMANDS)


def describe_limits(limits: RiskLimits) -> dict[str, str]:
    return {name: str(value) for name, value in limits.model_dump().items()}


@dataclass(frozen=True, kw_only=True)
class ApiComposer:
    database: Database
    clock: Clock
    sleeper: Sleeper
    ids: IdGenerator
    alerts: AlertSink
    account_id: str
    jwt_secret: bytes
    limits: RiskLimits
    cors_origins: tuple[str, ...] = ()
    commands_collection: str = Collection.COMMANDS
    kill_switch_collection: str = Collection.KILL_SWITCH

    def build(self) -> tuple[ApiServices, Any]:
        """The services, and a FastAPI lifespan that runs the change-stream wake alongside."""
        db = self.database
        commands = CommandRepository(db, self.commands_collection)
        wake = ChangeStreamWake(self._watch, self.sleeper, self.alerts)
        services = ApiServices(
            auth=AuthService(
                MongoPasscodeStore(UserRepository(db)),
                Argon2Passcodes(),
                TokenService(self.jwt_secret, self.clock),
                LoginThrottle(self.clock),
            ),
            queries=QueryService(
                self.account_id,
                orders=OrderRepository(db),
                events=OrderEventRepository(db),
                executions=ExecutionRepository(db),
                positions=PositionRepository(db),
                snapshots=PortfolioSnapshotRepository(db),
                strategies=StrategyRepository(db),
                runs=StrategyRunRepository(db),
                signals=SignalRepository(db),
                risk_events=RiskEventRepository(db),
                reconciliations=ReconciliationRunRepository(db),
                system_events=SystemEventRepository(db),
                kill_switch=KillSwitchRepository(db, self.kill_switch_collection),
                commands=commands,
                results=CommandResultRepository(db),
                risk_limits=describe_limits(self.limits),
            ),
            submitter=CommandSubmitter(commands, self.clock, self.ids),
            clock=self.clock,
            wake=wake,
            cors_origins=self.cors_origins,
        )

        @asynccontextmanager
        async def lifespan(_: FastAPI) -> AsyncIterator[None]:
            wake.start()
            try:
                yield
            finally:
                await wake.stop()

        return services, lifespan

    async def _watch(self) -> Any:
        pipeline = [{"$match": {"ns.coll": {"$in": [c.value for c in _WATCHED]}}}]
        return await self.database.watch(pipeline)
