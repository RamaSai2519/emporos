"""One repository per collection in plan.md §6.

Each subclass binds a collection to its record type and adds only the lookups
the platform actually needs (idempotency-key and tag resolution, hot reads).
Storage details — collection names, indexes, `Decimal128` — stay in here.
"""

from __future__ import annotations

from collections.abc import Collection as Sized
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from emporos.persistence.collections import Collection
from emporos.persistence.records import (
    AccountRecord,
    BacktestRunRecord,
    BacktestTradeRecord,
    CommandRecord,
    CommandResultRecord,
    ExecutionRecord,
    InstrumentRecord,
    InstrumentVersionRecord,
    KillSwitchRecord,
    MarketCalendarRecord,
    OrderEventRecord,
    OrderRecord,
    PortfolioSnapshotRecord,
    PositionRecord,
    QuarantineRecord,
    ReconciliationRunRecord,
    RiskEventRecord,
    SignalRecord,
    StrategyRecord,
    StrategyRunRecord,
    SystemEventRecord,
    UserRecord,
)
from emporos.persistence.repository import Repository

Database = AsyncDatabase[Mapping[str, Any]]


class UserRepository(Repository[UserRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.USERS, UserRecord)


class AccountRepository(Repository[AccountRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.ACCOUNTS, AccountRecord)

    async def get_by_client_code(self, client_code: str) -> AccountRecord | None:
        return await self.find_one({"client_code": client_code})


class InstrumentRepository(Repository[InstrumentRecord]):
    def __init__(self, database: Database, collection: str = Collection.INSTRUMENTS) -> None:
        super().__init__(database, collection, InstrumentRecord)

    async def all(self) -> list[InstrumentRecord]:
        return await self.find({})

    async def get_many(
        self, instrument_ids: Sequence[str], *, session: AsyncClientSession | None = None
    ) -> list[InstrumentRecord]:
        return await self.find({"_id": {"$in": list(instrument_ids)}}, session=session)

    async def get_by_token(self, exchange: str, token: str) -> InstrumentRecord | None:
        return await self.find_one({"exchange": exchange, "token": token})

    async def get_by_symbol(self, exchange: str, tradingsymbol: str) -> InstrumentRecord | None:
        return await self.find_one({"exchange": exchange, "tradingsymbol": tradingsymbol})


class InstrumentVersionRepository(Repository[InstrumentVersionRecord]):
    def __init__(
        self, database: Database, collection: str = Collection.INSTRUMENT_VERSIONS
    ) -> None:
        super().__init__(database, collection, InstrumentVersionRecord)

    async def for_instrument(self, instrument_id: str) -> list[InstrumentVersionRecord]:
        return await self.find({"instrument_id": instrument_id}, sort=[("valid_from", ASCENDING)])


class StrategyRepository(Repository[StrategyRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.STRATEGIES, StrategyRecord)

    async def get_by_name(self, name: str) -> StrategyRecord | None:
        return await self.find_one({"name": name})


class StrategyRunRepository(Repository[StrategyRunRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.STRATEGY_RUNS, StrategyRunRecord)


class SignalRepository(Repository[SignalRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.SIGNALS, SignalRecord)

    async def for_run(self, strategy_run_id: str) -> list[SignalRecord]:
        return await self.find(
            {"strategy_run_id": strategy_run_id},
            sort=[("ts", ASCENDING), ("sequence", ASCENDING)],
        )


class OrderRepository(Repository[OrderRecord]):
    def __init__(self, database: Database, collection: str = Collection.ORDERS) -> None:
        super().__init__(database, collection, OrderRecord)

    async def get_by_idempotency_key(self, idempotency_key: str) -> OrderRecord | None:
        return await self.find_one({"idempotency_key": idempotency_key})

    async def get_by_ordertag(self, ordertag: str) -> OrderRecord | None:
        return await self.find_one({"ordertag": ordertag})

    async def in_states(self, session_date: str, states: Sized[str]) -> list[OrderRecord]:
        return await self.find({"session_date": session_date, "state": {"$in": list(states)}})

    async def created_since(self, account_id: str, since: datetime) -> list[OrderRecord]:
        return await self.find(
            {"account_id": account_id, "created_at": {"$gte": since}},
            sort=[("created_at", ASCENDING)],
        )

    async def for_account_session(self, account_id: str, session_date: str) -> list[OrderRecord]:
        return await self.find(
            {"account_id": account_id, "session_date": session_date},
            sort=[("created_at", ASCENDING), ("_id", ASCENDING)],
        )


class OrderEventRepository(Repository[OrderEventRecord]):
    def __init__(self, database: Database, collection: str = Collection.ORDER_EVENTS) -> None:
        super().__init__(database, collection, OrderEventRecord)

    async def for_order(self, order_id: str) -> list[OrderEventRecord]:
        return await self.find({"order_id": order_id}, sort=[("seq", ASCENDING)])

    async def last_seq(self, order_id: str) -> int:
        """The order's highest event sequence number, 0 if it has no events."""
        latest = await self.find({"order_id": order_id}, sort=[("seq", -1)], limit=1)
        return latest[0].seq if latest else 0


class ExecutionRepository(Repository[ExecutionRecord]):
    def __init__(self, database: Database, collection: str = Collection.EXECUTIONS) -> None:
        super().__init__(database, collection, ExecutionRecord)

    async def get_by_broker_trade_id(self, broker_trade_id: str) -> ExecutionRecord | None:
        return await self.find_one({"broker_trade_id": broker_trade_id})

    async def for_order(self, order_id: str) -> list[ExecutionRecord]:
        return await self.find({"order_id": order_id}, sort=[("ts", ASCENDING)])

    async def for_account(self, account_id: str) -> list[ExecutionRecord]:
        """Every fill of an account, in the order it was booked: the whole history a position is
        the running total of."""
        return await self.find(
            {"account_id": account_id},
            sort=[("ts", ASCENDING), ("session_trade_no", ASCENDING), ("_id", ASCENDING)],
        )

    async def for_account_session(
        self, account_id: str, session_date: str
    ) -> list[ExecutionRecord]:
        """An account's fills for one session, in the order they were booked."""
        return await self.find(
            {"account_id": account_id, "session_date": session_date},
            sort=[("session_trade_no", ASCENDING), ("ts", ASCENDING)],
        )


class PositionRepository(Repository[PositionRecord]):
    def __init__(self, database: Database, collection: str = Collection.POSITIONS) -> None:
        super().__init__(database, collection, PositionRecord)

    async def get_for(self, account_id: str, instrument_id: str) -> PositionRecord | None:
        return await self.find_one({"account_id": account_id, "instrument_id": instrument_id})

    async def all_for_account(self, account_id: str) -> list[PositionRecord]:
        return await self.find({"account_id": account_id}, sort=[("instrument_id", ASCENDING)])

    async def open_for_account(self, account_id: str) -> list[PositionRecord]:
        return await self.find({"account_id": account_id, "net_quantity": {"$ne": 0}})


class PortfolioSnapshotRepository(Repository[PortfolioSnapshotRecord]):
    def __init__(
        self, database: Database, collection: str = Collection.PORTFOLIO_SNAPSHOTS
    ) -> None:
        super().__init__(database, collection, PortfolioSnapshotRecord)


class RiskEventRepository(Repository[RiskEventRecord]):
    def __init__(self, database: Database, collection: str = Collection.RISK_EVENTS) -> None:
        super().__init__(database, collection, RiskEventRecord)

    async def for_signal(self, signal_id: str) -> list[RiskEventRecord]:
        return await self.find({"signal_id": signal_id}, sort=[("ts", ASCENDING)])

    async def for_rule(self, rule: str) -> list[RiskEventRecord]:
        return await self.find({"rule": rule}, sort=[("ts", ASCENDING)])


class ReconciliationRunRepository(Repository[ReconciliationRunRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.RECONCILIATION_RUNS, ReconciliationRunRecord)


class BacktestRunRepository(Repository[BacktestRunRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.BACKTEST_RUNS, BacktestRunRecord)


class BacktestTradeRepository(Repository[BacktestTradeRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.BACKTEST_TRADES, BacktestTradeRecord)


class SystemEventRepository(Repository[SystemEventRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.SYSTEM_EVENTS, SystemEventRecord)


class MarketCalendarRepository(Repository[MarketCalendarRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.MARKET_CALENDAR, MarketCalendarRecord)

    async def get_by_date(self, date: str) -> MarketCalendarRecord | None:
        return await self.find_one({"date": date})


class QuarantineRepository(Repository[QuarantineRecord]):
    def __init__(self, database: Database) -> None:
        super().__init__(database, Collection.CORPORATE_ACTION_QUARANTINE, QuarantineRecord)


class KillSwitchRepository(Repository[KillSwitchRecord]):
    """The kill switch is a single document; `CURRENT_ID` is its fixed `_id`."""

    CURRENT_ID = "kill_switch"

    def __init__(self, database: Database, collection: str = Collection.KILL_SWITCH) -> None:
        super().__init__(database, collection, KillSwitchRecord)

    async def current(self) -> KillSwitchRecord | None:
        return await self.get(self.CURRENT_ID)

    async def save(
        self, record: KillSwitchRecord, *, session: AsyncClientSession | None = None
    ) -> None:
        await self.replace(record, upsert=True, session=session)


class CommandRepository(Repository[CommandRecord]):
    def __init__(self, database: Database, collection: str = Collection.COMMANDS) -> None:
        super().__init__(database, collection, CommandRecord)

    async def get_by_idempotency_key(self, idempotency_key: str) -> CommandRecord | None:
        return await self.find_one({"idempotency_key": idempotency_key})

    async def with_status(self, status: str) -> list[CommandRecord]:
        return await self.find({"status": status}, sort=[("created_at", ASCENDING)])

    async def in_statuses(self, statuses: Sequence[str]) -> list[CommandRecord]:
        return await self.find(
            {"status": {"$in": list(statuses)}},
            sort=[("created_at", ASCENDING), ("_id", ASCENDING)],
        )

    async def transition(
        self,
        command_id: str,
        allowed_from: Sequence[str],
        to: str,
        at: datetime,
        *,
        reason: str = "",
        count_attempt: bool = False,
    ) -> CommandRecord | None:
        """Atomically move one command from any of `allowed_from` to `to`. Returns the command as
        it now stands, or None if it was not in an allowed state — so two workers racing for the
        same command cannot both win it."""
        update: dict[str, Any] = {
            "$set": {"status": to, "updated_at": at, "reason": reason},
        }
        if count_attempt:
            update["$inc"] = {"attempts": 1}
        document = await self._collection.find_one_and_update(
            {"_id": command_id, "status": {"$in": list(allowed_from)}},
            update,
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else CommandRecord.model_validate(document)

    async def updated_since(self, since: datetime, limit: int = 500) -> list[CommandRecord]:
        return await self.find(
            {"updated_at": {"$gt": since}}, sort=[("updated_at", ASCENDING)], limit=limit
        )


class CommandResultRepository(Repository[CommandResultRecord]):
    def __init__(self, database: Database, collection: str = Collection.COMMAND_RESULTS) -> None:
        super().__init__(database, collection, CommandResultRecord)

    async def for_command(self, command_id: str) -> list[CommandResultRecord]:
        return await self.find({"command_id": command_id})
