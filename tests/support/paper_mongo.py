"""Real-Atlas plumbing for paper-broker persistence tests: repositories, journal, store, and
cleanup of everything a test wrote. Every id is randomly suffixed (plan.md §6.0: the dev database
is shared) and only this rig's own scratch account is ever deleted."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.ids import IdGenerator
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.paper_journal import MongoPaperJournal, MongoPaperSessionStore
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderEventRepository,
    OrderRepository,
    PortfolioSnapshotRepository,
    PositionRepository,
)
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.persistence.transactions import FillApplier, TransactionRunner


class PaperMongoRig:
    def __init__(
        self,
        client: AsyncMongoClient[Mapping[str, Any]],
        database: AsyncDatabase[Mapping[str, Any]],
        fills: FillApplier | None = None,
    ) -> None:
        self.database = database
        self.account_id = f"PAPERIT{IdGenerator().new_ulid()}"
        self.orders = OrderRepository(database)
        self.events = OrderEventRepository(database)
        self.executions = ExecutionRepository(database)
        self.positions = PositionRepository(database)
        self.snapshots = PortfolioSnapshotRepository(database)
        self.fills = fills or FillApplier(
            TransactionRunner(client), self.orders, self.executions, self.positions
        )
        self.store = MongoPaperSessionStore(self.orders, self.events, self.executions)

    async def prepare(self) -> None:
        await MigrationRunner(MongoSchemaStore(self.database), PLATFORM_SCHEMA).apply()

    def journal(self) -> MongoPaperJournal:
        return MongoPaperJournal(
            self.orders, self.events, self.fills, self.positions, self.snapshots
        )

    async def cleanup(self) -> None:
        """Delete this rig's scratch rows: its account's, and nothing else."""
        mine = {"account_id": self.account_id}
        order_ids = [r.id for r in await self.orders.find(mine)]
        for collection in (
            Collection.ORDERS, Collection.EXECUTIONS, Collection.POSITIONS,
            Collection.PORTFOLIO_SNAPSHOTS,
        ):  # fmt: skip
            await self.database[collection].delete_many(mine)
        await self.database[Collection.ORDER_EVENTS].delete_many({"order_id": {"$in": order_ids}})
