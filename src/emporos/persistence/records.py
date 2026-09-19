"""Typed records — one per collection in plan.md §6.

A record models the fields the platform relies on (identity, unique keys, state,
money); `extra="allow"` carries the rest untouched so later phases can grow a
collection's shape without a migration of this layer. Records are immutable
value objects: state changes are new records written back through a repository.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from emporos.domain.orders import OrderSide, OrderType
from emporos.persistence.money_codec import MoneyField


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow", populate_by_name=True)

    id: str = Field(alias="_id")

    def to_document(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class UserRecord(Record):
    pass


class AccountRecord(Record):
    client_code: str
    mode: str = "paper"


class InstrumentRecord(Record):
    """The current definition of an instrument, in force since `valid_from`."""

    exchange: str
    token: str
    tradingsymbol: str
    name: str
    lot_size: int
    tick_size: MoneyField
    valid_from: datetime


class InstrumentVersionRecord(Record):
    """A superseded definition, in force over `[valid_from, valid_to)`. Append-only."""

    instrument_id: str
    exchange: str
    token: str
    tradingsymbol: str
    name: str
    lot_size: int
    tick_size: MoneyField
    valid_from: datetime
    valid_to: datetime


class StrategyRecord(Record):
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class StrategyRunRecord(Record):
    strategy_id: str
    session_date: str
    created_at: datetime
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


class SignalRecord(Record):
    strategy_run_id: str
    instrument_id: str
    ts: datetime


class OrderRecord(Record):
    idempotency_key: str
    ordertag: str
    instrument_id: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: MoneyField
    trigger_price: MoneyField | None = None
    state: str
    session_date: str
    broker_order_id: str | None = None
    filled_quantity: int = 0
    created_at: datetime
    updated_at: datetime


class OrderEventRecord(Record):
    order_id: str
    seq: int
    ts: datetime
    state: str


class ExecutionRecord(Record):
    broker_trade_id: str
    order_id: str
    instrument_id: str
    side: OrderSide
    quantity: int
    price: MoneyField
    ts: datetime


class PositionRecord(Record):
    account_id: str
    instrument_id: str
    net_quantity: int
    average_price: MoneyField
    realised_pnl: MoneyField
    updated_at: datetime


class PortfolioSnapshotRecord(Record):
    account_id: str
    ts: datetime


class RiskEventRecord(Record):
    rule: str
    ts: datetime


class ReconciliationRunRecord(Record):
    status: str
    ts: datetime


class BacktestRunRecord(Record):
    strategy_id: str
    created_at: datetime


class BacktestTradeRecord(Record):
    backtest_run_id: str


class SystemEventRecord(Record):
    type: str
    correlation_id: str | None = None
    ts: datetime


class MarketCalendarRecord(Record):
    date: str
    is_trading_day: bool = True


class KillSwitchRecord(Record):
    halted: bool = False


class CommandRecord(Record):
    idempotency_key: str
    type: str
    status: str
    created_at: datetime


class CommandResultRecord(Record):
    command_id: str
    created_at: datetime
