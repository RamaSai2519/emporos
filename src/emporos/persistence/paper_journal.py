"""The paper broker's storage adapter: simulated trading persisted through the SAME repositories
real trading uses (orders, order_events, executions, positions, portfolio_snapshots) — paper is
not a lesser-tracked code path (plan.md §6, §12).

* `MongoPaperJournal` implements `PaperJournal`: `append` queues in order; `flush` writes the
  queue front-to-back and pops an entry only once it is durable. A failed write leaves it queued,
  so the next flush retries it. Every write is idempotent — orders are replaced by id, order
  events are unique on `(order_id, seq)`, fills on `broker_trade_id`, and a fill's execution +
  order + position go through `FillApplier`, the one multi-document transaction — so a retry or a
  replay can never duplicate an order, an event or a fill.
* `MongoPaperSessionStore` implements `PaperSessionStore`: it reads a session back so a restart
  resumes it exactly.

Storage: these are the paper broker's OWN books (`paper_*` collections, `PaperBooks`). The
platform's `orders`/`order_events`/`executions`/`positions` belong to the execution layer alone,
which fills them from what this broker reports — one writer per collection (EM-99 G6). An order
that already carries the same `ordertag` in the paper books is updated in place rather than
inserted twice, so a replayed placement never duplicates.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime

from emporos.broker.models import BrokerOrder, BrokerOrderStatus, BrokerTrade
from emporos.broker.paper.exchange import RestoredOrder
from emporos.broker.paper.journal import (
    FillRecorded,
    JournalEntry,
    OrderStateRecorded,
    RestoredFill,
    RestoredSession,
    SnapshotRecorded,
)
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import (
    ExecutionRecord,
    OrderEventRecord,
    OrderRecord,
    PortfolioSnapshotRecord,
    PositionRecord,
    PositionSnapshot,
)
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderEventRepository,
    OrderRepository,
    PortfolioSnapshotRepository,
    PositionRepository,
)
from emporos.persistence.transactions import FillApplication, FillApplier


class MongoPaperJournal:
    def __init__(
        self,
        orders: OrderRepository,
        events: OrderEventRepository,
        fills: FillApplier,
        positions: PositionRepository,
        snapshots: PortfolioSnapshotRepository,
    ) -> None:
        self._orders = orders
        self._events = events
        self._fills = fills
        self._positions = positions
        self._snapshots = snapshots
        self._queue: deque[JournalEntry] = deque()
        self._lock = asyncio.Lock()
        self._writers: dict[type[JournalEntry], Callable[..., Awaitable[None]]] = {
            OrderStateRecorded: self._write_order_state,
            FillRecorded: self._write_fill,
            SnapshotRecorded: self._write_snapshot,
        }

    @property
    def pending(self) -> int:
        """Entries appended but not yet durable."""
        return len(self._queue)

    def append(self, entry: JournalEntry) -> None:
        self._queue.append(entry)

    async def flush(self) -> None:
        async with self._lock:
            while self._queue:
                entry = self._queue[0]
                await self._writers[type(entry)](entry)
                self._queue.popleft()

    # --- writers ---------------------------------------------------------------------------
    async def _write_order_state(self, entry: OrderStateRecorded) -> None:
        existing = await self._orders.get_by_ordertag(_tag(entry.order))
        record = self._order_record(
            entry.account_id, entry.session_date, entry.order, entry.at, existing
        )
        await self._orders.replace(record, upsert=True)
        await self._append_event(record.id, entry.seq, entry.at, entry.order, entry.reason)

    async def _write_fill(self, entry: FillRecorded) -> None:
        existing = await self._orders.get_by_ordertag(_tag(entry.order))
        record = self._order_record(
            entry.account_id, entry.session_date, entry.order, entry.at, existing
        )
        if existing is None:  # FillApplier only replaces: the order must exist first
            await self._orders.replace(record, upsert=True)
        position = entry.position
        held = await self._positions.get_for(entry.account_id, position.instrument_id)
        # A DUPLICATE outcome means this fill is already applied: the goal state is reached.
        await self._fills.apply(
            FillApplication(
                execution=ExecutionRecord(
                    _id=f"exec-{entry.trade.trade_id}",
                    broker_trade_id=entry.trade.trade_id,
                    order_id=record.id,
                    instrument_id=entry.trade.instrument_id,
                    side=entry.trade.side,
                    quantity=entry.trade.quantity,
                    price=entry.trade.price,
                    ts=entry.at,
                    account_id=entry.account_id,
                    session_date=entry.session_date,
                    fees=entry.fees,
                    session_trade_no=entry.number,
                ),
                order=record,
                position=PositionRecord(
                    _id=held.id if held else f"{entry.account_id}:{position.instrument_id}",
                    account_id=entry.account_id,
                    instrument_id=position.instrument_id,
                    net_quantity=position.net_quantity,
                    average_price=position.average_price,
                    realised_pnl=position.realised,
                    updated_at=entry.at,
                    gross_realised_pnl=position.gross_realised,
                    fees=position.fees,
                    session_date=entry.session_date,
                ),
            )
        )
        await self._append_event(record.id, entry.seq, entry.at, entry.order, "")

    async def _write_snapshot(self, entry: SnapshotRecorded) -> None:
        record = PortfolioSnapshotRecord(
            _id=f"{entry.account_id}:{entry.at.isoformat()}:{entry.trades}",
            account_id=entry.account_id,
            ts=entry.at,
            session_date=entry.session_date,
            cash=entry.cash,
            realised_pnl=entry.realised,
            unrealised_pnl=entry.unrealised,
            fees=entry.fees,
            trades=entry.trades,
            positions=[
                PositionSnapshot(
                    instrument_id=p.instrument_id,
                    net_quantity=p.net_quantity,
                    average_price=p.average_price,
                    realised_pnl=p.realised,
                    fees=p.fees,
                )
                for p in entry.positions
            ],
        )
        try:
            await self._snapshots.insert(record)
        except DuplicateRecordError:
            return  # the same account state at the same instant: already written

    # --- helpers ---------------------------------------------------------------------------
    async def _append_event(
        self, order_id: str, seq: int, at: datetime, order: BrokerOrder, reason: str
    ) -> None:
        try:
            await self._events.insert(
                OrderEventRecord(
                    _id=f"{order_id}:{seq}",
                    order_id=order_id,
                    seq=seq,
                    ts=at,
                    state=order.status.value,
                    filled_quantity=order.filled_quantity,
                    reason=reason,
                )
            )
        except DuplicateRecordError:
            return  # this transition is already recorded

    @staticmethod
    def _order_record(
        account_id: str,
        session_date: str,
        order: BrokerOrder,
        at: datetime,
        existing: OrderRecord | None,
    ) -> OrderRecord:
        assert order.order_type is not None and order.price is not None
        return OrderRecord(
            _id=existing.id if existing else order.broker_order_id,
            idempotency_key=existing.idempotency_key
            if existing
            else f"paper-{order.broker_order_id}",
            ordertag=_tag(order),
            instrument_id=order.instrument_id,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            limit_price=order.price,
            trigger_price=order.trigger_price,
            state=order.status.value,
            session_date=session_date,
            broker_order_id=order.broker_order_id,
            filled_quantity=order.filled_quantity,
            created_at=existing.created_at if existing else at,
            updated_at=order.updated_at or at,
            account_id=account_id,
            average_price=order.average_price,
            status_message=order.status_message,
        )


def _tag(order: BrokerOrder) -> str:
    if order.client_tag is None:
        raise ValueError("a paper order always carries its client tag")
    return order.client_tag


class MongoPaperSessionStore:
    def __init__(
        self,
        orders: OrderRepository,
        events: OrderEventRepository,
        executions: ExecutionRepository,
    ) -> None:
        self._orders = orders
        self._events = events
        self._executions = executions

    async def load(self, account_id: str, session_date: str) -> RestoredSession:
        records = await self._orders.for_account_session(account_id, session_date)
        executions = await self._executions.for_account_session(account_id, session_date)
        broker_ids = {r.id: r.broker_order_id or r.id for r in records}
        trades_per_order: dict[str, int] = {}
        fills: list[RestoredFill] = []
        for execution in executions:
            if execution.fees is None:
                raise ValueError(f"execution {execution.broker_trade_id} has no recorded charges")
            trades_per_order[execution.order_id] = trades_per_order.get(execution.order_id, 0) + 1
            fills.append(
                RestoredFill(
                    BrokerTrade(
                        execution.broker_trade_id,
                        broker_ids[execution.order_id],
                        execution.instrument_id,
                        execution.side,
                        execution.quantity,
                        execution.price,
                        execution.ts,
                    ),
                    execution.fees,
                )
            )
        orders = [
            RestoredOrder(
                self._broker_order(record),
                await self._events.last_seq(record.id),
                trades_per_order.get(record.id, 0),
            )
            for record in records
        ]
        return RestoredSession(orders, fills)

    @staticmethod
    def _broker_order(record: OrderRecord) -> BrokerOrder:
        return BrokerOrder(
            broker_order_id=record.broker_order_id or record.id,
            client_tag=record.ordertag,
            instrument_id=record.instrument_id,
            side=record.side,
            order_type=record.order_type,
            quantity=record.quantity,
            filled_quantity=record.filled_quantity,
            status=BrokerOrderStatus(record.state),
            price=record.limit_price,
            trigger_price=record.trigger_price,
            average_price=record.average_price,
            status_message=record.status_message,
            updated_at=record.updated_at,
        )
