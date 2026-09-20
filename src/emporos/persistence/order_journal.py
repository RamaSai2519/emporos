"""The one writer of platform order state: orders, their audit events, fills and positions.

Every change to an order lands in the same transaction as its `order_events` row (monotonic
`seq`), so the event log is a complete history. A fill additionally writes its execution and the
resulting position in that same transaction, which is what makes fill processing atomic and — via
the unique `broker_trade_id` index — idempotent.
"""

from datetime import datetime

from pymongo.asynchronous.client_session import AsyncClientSession

from emporos.persistence.errors import ConcurrentModificationError, DuplicateRecordError
from emporos.persistence.records import (
    ExecutionRecord,
    OrderEventRecord,
    OrderRecord,
    PositionRecord,
)
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderEventRepository,
    OrderRepository,
    PositionRepository,
)
from emporos.persistence.transactions import FillOutcome, TransactionRunner


class MongoOrderJournal:
    def __init__(
        self,
        orders: OrderRepository,
        events: OrderEventRepository,
        executions: ExecutionRepository,
        positions: PositionRepository,
        transactions: TransactionRunner,
        account_id: str,
    ) -> None:
        self._orders = orders
        self._events = events
        self._executions = executions
        self._positions = positions
        self._transactions = transactions
        self._account_id = account_id

    async def get(self, order_id: str) -> OrderRecord | None:
        return await self._orders.find_one({"_id": order_id, "account_id": self._account_id})

    async def by_key(self, key: str) -> OrderRecord | None:
        return await self._orders.find_one({"idempotency_key": key, "account_id": self._account_id})

    async def by_broker_order_id(self, broker_order_id: str) -> OrderRecord | None:
        return await self._orders.find_one(
            {"broker_order_id": broker_order_id, "account_id": self._account_id}
        )

    async def history(self, order_id: str) -> list[OrderEventRecord]:
        return await self._events.for_order(order_id)

    async def unresolved(self, instrument_id: str) -> bool:
        return bool(
            await self._orders.count(
                {
                    "account_id": self._account_id,
                    "instrument_id": instrument_id,
                    "state": {"$in": ["UNKNOWN", "PENDING_NEW"]},
                }
            )
        )

    async def active(self) -> list[OrderRecord]:
        return await self._orders.find(
            {
                "account_id": self._account_id,
                "state": {"$nin": ["FILLED", "CANCELLED", "REJECTED"]},
            },
            sort=[("created_at", 1), ("_id", 1)],
        )

    async def create(self, order: OrderRecord) -> None:
        if order.account_id != self._account_id:
            raise ValueError("order does not belong to this journal's account")

        async def write(session: AsyncClientSession) -> None:
            await self._orders.insert(order, session=session)
            await self._events.insert(self._event(order, 1), session=session)

        await self._transactions.run(write)

    async def transition(self, previous: OrderRecord, updated: OrderRecord) -> None:
        if (
            previous.account_id != self._account_id
            or updated.account_id != self._account_id
            or previous.id != updated.id
            or previous.idempotency_key != updated.idempotency_key
            or previous.ordertag != updated.ordertag
        ):
            raise ValueError("an order transition cannot change ownership or identity")

        async def write(session: AsyncClientSession) -> None:
            await self._change(previous, updated, session)

        await self._transactions.run(write)

    async def has_execution(self, broker_trade_id: str) -> bool:
        return await self._executions.get_by_broker_trade_id(broker_trade_id) is not None

    async def position(self, instrument_id: str) -> PositionRecord | None:
        return await self._positions.get_for(self._account_id, instrument_id)

    async def fills_this_session(self, session_date: str) -> int:
        return len(await self._executions.for_account_session(self._account_id, session_date))

    async def record_fill(
        self,
        previous: OrderRecord,
        updated: OrderRecord,
        execution: ExecutionRecord,
        position: PositionRecord,
    ) -> FillOutcome:
        if execution.account_id != self._account_id or updated.account_id != self._account_id:
            raise ValueError("a fill cannot cross accounts")

        async def write(session: AsyncClientSession) -> None:
            await self._executions.insert(execution, session=session)
            await self._change(previous, updated, session)
            await self._positions.replace(position, upsert=True, session=session)

        try:
            await self._transactions.run(write)
        except DuplicateRecordError as error:
            if error.key_fields == ("broker_trade_id",):
                return FillOutcome.DUPLICATE
            raise
        return FillOutcome.APPLIED

    async def _change(
        self, previous: OrderRecord, updated: OrderRecord, session: AsyncClientSession
    ) -> None:
        current = await self._orders.find_one({"_id": previous.id}, session=session)
        if current is None or self._version(current) != self._version(previous):
            raise ConcurrentModificationError("order changed concurrently; reload and retry")
        events = await self._events.find(
            {"order_id": previous.id}, sort=[("seq", -1)], limit=1, session=session
        )
        if not events:
            raise ValueError("an order without an audit event cannot transition")
        await self._orders.replace(updated, session=session)
        await self._events.insert(self._event(updated, events[0].seq + 1), session=session)

    @staticmethod
    def _version(order: OrderRecord) -> tuple[str, int, int, str | None, datetime]:
        # BSON stores millisecond timestamps. Comparing complete Python records would
        # reject legitimate updates whenever the clock includes sub-millisecond precision.
        return (
            order.state,
            order.filled_quantity,
            order.absence_checks,
            order.broker_order_id,
            order.updated_at.replace(microsecond=order.updated_at.microsecond // 1000 * 1000),
        )

    @staticmethod
    def _event(order: OrderRecord, seq: int) -> OrderEventRecord:
        return OrderEventRecord(
            _id=f"{order.id}:{seq}",
            order_id=order.id,
            seq=seq,
            ts=order.updated_at,
            state=order.state,
            filled_quantity=order.filled_quantity,
            reason=order.status_message,
        )
