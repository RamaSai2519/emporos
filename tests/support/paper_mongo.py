"""Real-Atlas plumbing for paper-broker persistence tests: repositories, journal, store, and
cleanup of everything a test wrote. Every id is randomly suffixed (plan.md §6.0: the dev database
is shared) and only this rig's own scratch account is ever deleted."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from emporos.broker.base import Broker
from emporos.broker.models import BrokerOrderStatus
from emporos.broker.paper.account import PaperAccount
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.paper_books import PaperBooks
from emporos.persistence.paper_journal import MongoPaperJournal
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
        self.books = PaperBooks.in_database(database)
        self.orders = self.books.orders
        self.events = self.books.events
        self.executions = self.books.executions
        self.positions = self.books.positions
        self.snapshots = self.books.snapshots
        self.fills = fills or FillApplier(
            TransactionRunner(client), self.orders, self.executions, self.positions
        )
        self.store = self.books.store()

    async def prepare(self) -> None:
        await MigrationRunner(MongoSchemaStore(self.database), PLATFORM_SCHEMA).apply()

    def journal(self) -> MongoPaperJournal:
        return self.books.journal_with(self.fills)

    async def cleanup(self) -> None:
        """Delete this rig's scratch rows: its account's, and nothing else."""
        mine = {"account_id": self.account_id}
        order_ids = [r.id for r in await self.orders.find(mine)]
        for collection in (
            Collection.PAPER_ORDERS, Collection.PAPER_EXECUTIONS, Collection.PAPER_POSITIONS,
            Collection.PAPER_PORTFOLIO_SNAPSHOTS,
        ):  # fmt: skip
            await self.database[collection].delete_many(mine)
        await self.database[Collection.PAPER_ORDER_EVENTS].delete_many(
            {"order_id": {"$in": order_ids}}
        )


async def assert_session_reconstructs(
    rig: PaperMongoRig, broker: Broker, day: str, starting_cash: Money
) -> None:
    """The acceptance test in one place: rebuild the session from persisted state ALONE and
    compare it with what the live broker says."""
    orders = await rig.orders.for_account_session(rig.account_id, day)
    executions = await rig.executions.for_account_session(rig.account_id, day)
    replay = PaperAccount(starting_cash)
    restored = await rig.store.load(rig.account_id, day)
    for fill in restored.fills:
        replay.apply(fill.trade, fill.fees)

    live_book = await broker.get_order_book()
    assert sorted((o.broker_order_id, o.status, o.filled_quantity) for o in live_book) == sorted(
        (o.broker_order_id or o.id, BrokerOrderStatus(o.state), o.filled_quantity) for o in orders
    )
    assert [t.trade_id for t in await broker.get_trade_book()] == [
        x.broker_trade_id for x in executions
    ]
    stored_positions = await rig.positions.find({"account_id": rig.account_id})  # closed ones too
    assert {p.instrument_id for p in stored_positions} == {
        p.instrument_id for p in replay.positions()
    }
    for position in stored_positions:
        rebuilt = replay.position(position.instrument_id)
        assert rebuilt is not None
        assert (position.net_quantity, position.average_price, position.realised_pnl) == (
            rebuilt.net_quantity, rebuilt.average_price, rebuilt.realised,
        )  # fmt: skip
    for record in orders:  # every order's event log is contiguous from 1 and ends in its state
        events = await rig.events.for_order(record.id)
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert events[-1].state == record.state
