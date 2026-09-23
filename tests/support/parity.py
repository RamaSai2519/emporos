"""Builders for parity tests: paper records with sensible defaults, and journal values."""

from __future__ import annotations

from datetime import datetime, timedelta

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.parity.inputs import PaperRunData
from emporos.persistence.records import (
    ExecutionRecord,
    OrderEventRecord,
    OrderRecord,
    RiskEventRecord,
    SignalRecord,
    StrategyRunRecord,
)
from tests.support.strategies import INSTRUMENT, T0

RUN = "run-1"


def paper_run(session_date: str = "2026-01-05") -> StrategyRunRecord:
    return StrategyRunRecord(
        _id=RUN, strategy_id="s-1", session_date=session_date, created_at=T0, strategy_name="s"
    )


def signal_record(
    signal_id: str = "sig-1",
    ts: datetime = T0,
    kind: str = "ENTRY",
    side: OrderSide = OrderSide.BUY,
    price: str = "100",
    quantity: int = 10,
    sequence: int = 1,
    instrument_id: str = INSTRUMENT,
    **extra: object,
) -> SignalRecord:
    return SignalRecord(
        _id=signal_id, strategy_run_id=RUN, instrument_id=instrument_id, ts=ts, kind=kind,
        side=side, order_type=OrderType.LIMIT, price=Money.of(price), quantity=quantity,
        sequence=sequence, reason="test", **extra,
    )  # fmt: skip


def order_record(
    order_id: str = "ord-1",
    signal_id: str | None = "sig-1",
    quantity: int = 10,
    created_at: datetime = T0,
    parent: str | None = None,
    state: str = "FILLED",
    side: OrderSide = OrderSide.BUY,
) -> OrderRecord:
    return OrderRecord(
        _id=order_id, signal_id=signal_id, strategy_run_id=RUN, parent_order_id=parent,
        idempotency_key=f"key-{order_id}", ordertag=f"tag-{order_id}", instrument_id=INSTRUMENT,
        side=side, order_type=OrderType.LIMIT, quantity=quantity, limit_price=Money.of("100"),
        state=state, session_date="2026-01-05", created_at=created_at, updated_at=created_at,
    )  # fmt: skip


def event(order_id: str, seq: int, ts: datetime, state: str) -> OrderEventRecord:
    return OrderEventRecord(_id=f"{order_id}-{seq}", order_id=order_id, seq=seq, ts=ts, state=state)


def execution(
    trade_id: str,
    order_id: str = "ord-1",
    quantity: int = 10,
    price: str = "100.10",
    ts: datetime = T0 + timedelta(seconds=5),
    fees: str = "5",
    side: OrderSide = OrderSide.BUY,
) -> ExecutionRecord:
    return ExecutionRecord(
        _id=trade_id, broker_trade_id=trade_id, order_id=order_id, instrument_id=INSTRUMENT,
        side=side, quantity=quantity, price=Money.of(price), ts=ts, fees=Money.of(fees),
    )  # fmt: skip


def risk_event(signal_id: str = "sig-1") -> RiskEventRecord:
    return RiskEventRecord(
        _id=f"risk-{signal_id}", rule="MaxPositionValue", ts=T0, signal_id=signal_id
    )


def paper_data(
    signals: tuple[SignalRecord, ...] = (),
    orders: tuple[OrderRecord, ...] = (),
    events: tuple[OrderEventRecord, ...] = (),
    executions: tuple[ExecutionRecord, ...] = (),
    risk: tuple[RiskEventRecord, ...] = (),
) -> PaperRunData:
    return PaperRunData(paper_run(), signals, orders, events, executions, risk)
