"""The paper broker's own books: its orders, order events, fills, positions and snapshots.

A simulated exchange keeps its own records, exactly as a real broker does. The platform's
`orders`/`order_events`/`executions`/`positions` are written only by the execution layer, from what
the broker reports, so there is one writer per collection and reconciling the platform against the
paper broker is as meaningful as reconciling it against Angel One.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pymongo import AsyncMongoClient

from emporos.persistence.collections import Collection
from emporos.persistence.paper_journal import MongoPaperJournal, MongoPaperSessionStore
from emporos.persistence.repositories import (
    Database,
    ExecutionRepository,
    OrderEventRepository,
    OrderRepository,
    PortfolioSnapshotRepository,
    PositionRepository,
)
from emporos.persistence.transactions import FillApplier, TransactionRunner


@dataclass(frozen=True)
class PaperBooks:
    orders: OrderRepository
    events: OrderEventRepository
    executions: ExecutionRepository
    positions: PositionRepository
    snapshots: PortfolioSnapshotRepository

    @classmethod
    def in_database(cls, database: Database) -> PaperBooks:
        return cls(
            OrderRepository(database, Collection.PAPER_ORDERS),
            OrderEventRepository(database, Collection.PAPER_ORDER_EVENTS),
            ExecutionRepository(database, Collection.PAPER_EXECUTIONS),
            PositionRepository(database, Collection.PAPER_POSITIONS),
            PortfolioSnapshotRepository(database, Collection.PAPER_PORTFOLIO_SNAPSHOTS),
        )

    def journal(self, client: AsyncMongoClient[Mapping[str, Any]]) -> MongoPaperJournal:
        fills = FillApplier(TransactionRunner(client), self.orders, self.executions, self.positions)
        return self.journal_with(fills)

    def journal_with(self, fills: FillApplier) -> MongoPaperJournal:
        return MongoPaperJournal(self.orders, self.events, fills, self.positions, self.snapshots)

    def store(self) -> MongoPaperSessionStore:
        return MongoPaperSessionStore(self.orders, self.events, self.executions)
