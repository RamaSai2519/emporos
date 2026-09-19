"""Multi-document transactions — used in exactly one place: applying a fill (plan.md §6).

`TransactionRunner` owns session and retry mechanics; `FillApplier` composes
three repositories inside one transaction so a fill is all-or-nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession

from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import ExecutionRecord, OrderRecord, PositionRecord
from emporos.persistence.repositories import (
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
)

T = TypeVar("T")


class TransactionRunner:
    """Runs a callback in a transaction; transient errors retry it, so it must be re-runnable."""

    def __init__(self, client: AsyncMongoClient[Mapping[str, Any]]) -> None:
        self._client = client

    async def run(self, work: Callable[[AsyncClientSession], Coroutine[Any, Any, T]]) -> T:
        async with self._client.start_session() as session:
            return await session.with_transaction(work)


@dataclass(frozen=True)
class FillApplication:
    """The three writes that make up one fill, already computed by the caller."""

    execution: ExecutionRecord
    order: OrderRecord
    position: PositionRecord


class FillOutcome(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"


class FillApplier:
    """Insert execution + update order + upsert position, atomically and idempotently.

    A redelivered fill collides on the unique `broker_trade_id` index; that aborts
    the transaction, so nothing is applied twice and the outcome is `DUPLICATE`.
    """

    def __init__(
        self,
        transactions: TransactionRunner,
        orders: OrderRepository,
        executions: ExecutionRepository,
        positions: PositionRepository,
    ) -> None:
        self._transactions = transactions
        self._orders = orders
        self._executions = executions
        self._positions = positions

    async def apply(self, fill: FillApplication) -> FillOutcome:
        async def write(session: AsyncClientSession) -> None:
            await self._executions.insert(fill.execution, session=session)
            await self._orders.replace(fill.order, session=session)
            await self._positions.replace(fill.position, upsert=True, session=session)

        try:
            await self._transactions.run(write)
        except DuplicateRecordError as error:
            if await self._is_redelivery(error, fill):
                return FillOutcome.DUPLICATE
            raise
        return FillOutcome.APPLIED

    async def _is_redelivery(self, error: DuplicateRecordError, fill: FillApplication) -> bool:
        """The unique `broker_trade_id` index says so directly. A caller that derives the record
        `_id` from the trade id collides on `_id` first, which proves nothing by itself — so it
        only counts if an execution with this trade id really is stored."""
        if error.key_fields == ("broker_trade_id",):
            return True
        if error.key_fields == ("_id",):
            return (
                await self._executions.get_by_broker_trade_id(fill.execution.broker_trade_id)
                is not None
            )
        return False
