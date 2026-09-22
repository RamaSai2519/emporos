"""The API's response and request shapes.

Money is always an exact decimal STRING, never a float, and the dashboard displays what the worker
computed: nothing here does arithmetic on money.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Dto(BaseModel):
    model_config = ConfigDict(frozen=True)


class LoginRequest(BaseModel):
    passcode: str = Field(min_length=1, max_length=256)


class TokenResponse(_Dto):
    token: str
    expires_at: datetime


class KillSwitchDto(_Dto):
    halted: bool
    reason: str = ""
    set_by: str | None = None
    changed_at: datetime | None = None


class ReconciliationDto(_Dto):
    id: str
    ts: datetime
    status: str
    trigger: str | None = None
    discrepancies: list[dict[str, str]] = []
    healed: list[dict[str, str]] = []


class OverviewDto(_Dto):
    session_state: str | None
    session_state_at: datetime | None
    kill_switch: KillSwitchDto | None
    reconciliation: ReconciliationDto | None
    open_positions: int
    cash: str | None
    realised_pnl: str | None
    unrealised_pnl: str | None
    fees: str | None
    trades: int | None
    snapshot_at: datetime | None
    pending_commands: int
    trading_mode: str | None
    broker_healthy: bool | None
    feed_healthy: bool | None
    worker_healthy: bool | None


class PositionDto(_Dto):
    instrument_id: str
    net_quantity: int
    average_price: str
    realised_pnl: str
    fees: str | None
    updated_at: datetime


class OrderDto(_Dto):
    id: str
    ordertag: str
    instrument_id: str
    side: str
    order_type: str
    quantity: int
    filled_quantity: int
    limit_price: str
    average_price: str | None
    state: str
    strategy_run_id: str | None
    signal_id: str | None
    parent_order_id: str | None
    reprice_count: int
    status_message: str
    created_at: datetime
    updated_at: datetime


class OrderEventDto(_Dto):
    seq: int
    ts: datetime
    state: str
    filled_quantity: int | None
    reason: str


class OrderDetailDto(_Dto):
    order: OrderDto
    events: list[OrderEventDto]


class ExecutionDto(_Dto):
    id: str
    broker_trade_id: str
    order_id: str
    instrument_id: str
    side: str
    quantity: int
    price: str
    fees: str | None
    ts: datetime


class GateDto(_Dto):
    name: str
    outcome: Literal["pass", "fail", "unknown"]
    detail: str


class VerdictDto(_Dto):
    """What a curation concluded, as recorded by the curation run itself."""

    outcome: Literal["validated", "inconclusive", "rejected"]
    recorded_at: datetime
    capital: str
    first_day: str
    last_day: str
    experiment: str
    source: str
    gates: list[GateDto]
    notes: list[str]  # what differs between what was judged and what would run


class StrategyDto(_Dto):
    name: str
    config_hash: str | None
    last_run_id: str | None
    last_run_date: str | None
    signals_last_run: int
    enabled: bool  # the strategy's own `enabled` flag in its config
    status: Literal["running", "stopped"]  # running: its latest run has not stopped
    # The verdict judged against the strategy's CURRENT config: `stale` is a verdict for a config
    # that has since been edited, `none` is a strategy never curated.
    standing: Literal["validated", "inconclusive", "rejected", "stale", "none"]
    verdict: VerdictDto | None


class RiskEventDto(_Dto):
    id: str
    ts: datetime
    rule: str
    reason: str
    instrument_id: str | None
    strategy_run_id: str | None
    signal_id: str | None


class RiskDto(_Dto):
    limits: dict[str, str]
    recent_rejections: list[RiskEventDto]


class SystemEventDto(_Dto):
    id: str
    type: str
    ts: datetime
    data: dict[str, Any]


class CommandResultDto(_Dto):
    ts: datetime
    status: str | None
    message: str
    data: dict[str, Any]


class CommandDto(_Dto):
    id: str
    idempotency_key: str
    type: str
    status: str
    params: dict[str, Any]
    issued_by: str
    created_at: datetime
    updated_at: datetime | None
    expires_at: datetime | None
    reason: str
    attempts: int


class CommandDetailDto(_Dto):
    command: CommandDto
    results: list[CommandResultDto]


class SubmitCommandRequest(BaseModel):
    """`idempotency_key` is client-generated: the same key can never execute twice."""

    idempotency_key: str = Field(min_length=8, max_length=128)
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class HealthDto(_Dto):
    status: str
    time: datetime
