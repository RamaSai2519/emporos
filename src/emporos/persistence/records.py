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
    # The fully-resolved config the run used, and its content hash: the run is reproducible from
    # these alone, even after the YAML changes (plan.md §9).
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    config_hash: str | None = None
    strategy_name: str | None = None


class SignalRecord(Record):
    strategy_run_id: str
    instrument_id: str
    ts: datetime
    kind: str | None = None  # a strategy signal: ENTRY or EXIT
    price: MoneyField | None = None  # the limit price the strategy had in mind
    quantity: int | None = None  # a sizing hint; risk may reduce or reject it
    ordertag: str | None = None  # links the signal to the order it produced
    side: OrderSide | None = None
    order_type: OrderType | None = None
    trigger_price: MoneyField | None = None
    reason: str = ""
    sequence: int | None = None  # 1-based position within the run: orders same-timestamp signals


class OrderRecord(Record):
    strategy_run_id: str | None = None
    signal_id: str | None = None
    signal_kind: str | None = None  # ENTRY or EXIT: what a replacement order must be judged as
    parent_order_id: str | None = None
    reprice_count: int = 0
    absence_checks: int = 0  # consecutive broker look-ups that found no such order
    original_limit_price: MoneyField | None = None
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
    # Optional so the record fits every writer; the paper broker sets all of these.
    account_id: str | None = None
    average_price: MoneyField | None = None
    status_message: str = ""


class OrderEventRecord(Record):
    order_id: str
    seq: int
    ts: datetime
    state: str
    filled_quantity: int | None = None
    reason: str = ""


class ExecutionRecord(Record):
    broker_trade_id: str
    order_id: str
    instrument_id: str
    side: OrderSide
    quantity: int
    price: MoneyField
    ts: datetime
    account_id: str | None = None
    session_date: str | None = None
    fees: MoneyField | None = None
    # 1-based position of this fill among the account's fills that session: the replay order.
    session_trade_no: int | None = None


class PositionRecord(Record):
    account_id: str
    instrument_id: str
    net_quantity: int
    average_price: MoneyField
    realised_pnl: MoneyField  # net of charges
    updated_at: datetime
    gross_realised_pnl: MoneyField | None = None
    fees: MoneyField | None = None
    session_date: str | None = None


class PositionSnapshot(BaseModel):
    """One position inside a portfolio snapshot."""

    model_config = ConfigDict(frozen=True)

    instrument_id: str
    net_quantity: int
    average_price: MoneyField
    realised_pnl: MoneyField
    fees: MoneyField


class PortfolioSnapshotRecord(Record):
    account_id: str
    ts: datetime
    session_date: str | None = None
    kind: str | None = None  # INTRADAY (periodic) or EOD (end of session)
    cash: MoneyField | None = None
    realised_pnl: MoneyField | None = None
    unrealised_pnl: MoneyField | None = None
    fees: MoneyField | None = None
    trades: int | None = None
    positions: list[PositionSnapshot] = Field(default_factory=list)


class RiskEventRecord(Record):
    """One risk rejection: which rule blocked which signal, and the state it judged it against."""

    rule: str
    ts: datetime
    signal_id: str | None = None
    strategy_run_id: str | None = None
    instrument_id: str | None = None
    side: OrderSide | None = None
    kind: str | None = None
    quantity: int | None = None
    limit_price: MoneyField | None = None
    reason: str = ""
    details: dict[str, str] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)  # every rule run, in order
    snapshot: dict[str, Any] = Field(default_factory=dict)  # the state the rules judged


class ReconciliationRunRecord(Record):
    status: str  # CLEAN, HEALED or FAILED
    ts: datetime
    trigger: str | None = None  # STARTUP, PERIODIC, EOD or MANUAL
    discrepancies: list[dict[str, str]] = Field(default_factory=list)  # what was found
    healed: list[dict[str, str]] = Field(default_factory=list)  # what was safely adopted


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
    """The kill switch: one document. `halted` is what the worker polls; the rest is who and why."""

    halted: bool = False
    reason: str = ""
    set_by: str | None = None
    changed_at: datetime | None = None


class CommandRecord(Record):
    idempotency_key: str
    type: str
    status: str
    created_at: datetime


class CommandResultRecord(Record):
    command_id: str
    created_at: datetime
