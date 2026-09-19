"""Builds valid, uniquely-keyed records for repository tests on the shared dev database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.persistence import records as r

NOW = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


class RecordFactory:
    """Every unique-index field carries a per-instance random suffix (plan.md §6.0)."""

    def __init__(self) -> None:
        self._run = IdGenerator().new_ulid()
        self._counter = 0

    def _next(self, label: str) -> str:
        self._counter += 1
        return f"{label}-{self._run}-{self._counter}"

    def user(self) -> r.UserRecord:
        return r.UserRecord(_id=self._next("user"))

    def account(self) -> r.AccountRecord:
        return r.AccountRecord(_id=self._next("acct"), client_code=self._next("client"))

    def instrument(self) -> r.InstrumentRecord:
        return r.InstrumentRecord(
            _id=self._next("inst"),
            exchange="NSE",
            token=self._next("tok"),
            tradingsymbol=self._next("SYM"),
            name="Test Co",
            instrument_type="EQ",
            lot_size=1,
            tick_size=Money.of("0.05"),
        )

    def instrument_version(self) -> r.InstrumentVersionRecord:
        return r.InstrumentVersionRecord(
            _id=self._next("iv"),
            exchange="NSE",
            token=self._next("tok"),
            tradingsymbol="SYM",
            valid_from=NOW,
        )

    def strategy(self) -> r.StrategyRecord:
        return r.StrategyRecord(_id=self._next("strat"), name=self._next("momentum"))

    def strategy_run(self) -> r.StrategyRunRecord:
        return r.StrategyRunRecord(
            _id=self._next("run"), strategy_id="s", session_date="2026-01-05", created_at=NOW
        )

    def signal(self, strategy_run_id: str | None = None) -> r.SignalRecord:
        return r.SignalRecord(
            _id=self._next("sig"),
            strategy_run_id=strategy_run_id or self._next("run"),
            instrument_id="i",
            ts=NOW,
        )

    def order(self, **overrides: Any) -> r.OrderRecord:
        fields: dict[str, Any] = {
            "_id": self._next("ord"),
            "idempotency_key": self._next("idem"),
            "ordertag": self._next("tag"),
            "instrument_id": "inst-1",
            "side": OrderSide.BUY,
            "order_type": OrderType.LIMIT,
            "quantity": 10,
            "limit_price": Money.of("101.35"),
            "state": "PENDING_NEW",
            "session_date": "2026-01-05",
            "created_at": NOW,
            "updated_at": NOW,
        }
        return r.OrderRecord(**{**fields, **overrides})

    def order_event(self, order_id: str | None = None, seq: int = 1) -> r.OrderEventRecord:
        return r.OrderEventRecord(
            _id=self._next("oe"),
            order_id=order_id or self._next("ord"),
            seq=seq,
            ts=NOW,
            state="PENDING_NEW",
        )

    def execution(self, **overrides: Any) -> r.ExecutionRecord:
        fields: dict[str, Any] = {
            "_id": self._next("exec"),
            "broker_trade_id": self._next("trade"),
            "order_id": self._next("ord"),
            "instrument_id": "inst-1",
            "side": OrderSide.BUY,
            "quantity": 10,
            "price": Money.of("101.35"),
            "ts": NOW,
        }
        return r.ExecutionRecord(**{**fields, **overrides})

    def position(self, **overrides: Any) -> r.PositionRecord:
        fields: dict[str, Any] = {
            "_id": self._next("pos"),
            "account_id": self._next("acct"),
            "instrument_id": self._next("inst"),
            "net_quantity": 10,
            "average_price": Money.of("101.35"),
            "realised_pnl": Money.zero(),
            "updated_at": NOW,
        }
        return r.PositionRecord(**{**fields, **overrides})

    def portfolio_snapshot(self) -> r.PortfolioSnapshotRecord:
        return r.PortfolioSnapshotRecord(_id=self._next("snap"), account_id="a", ts=NOW)

    def risk_event(self) -> r.RiskEventRecord:
        return r.RiskEventRecord(_id=self._next("risk"), rule="max_position", ts=NOW)

    def reconciliation_run(self) -> r.ReconciliationRunRecord:
        return r.ReconciliationRunRecord(_id=self._next("rec"), status="OK", ts=NOW)

    def backtest_run(self) -> r.BacktestRunRecord:
        return r.BacktestRunRecord(_id=self._next("bt"), strategy_id="s", created_at=NOW)

    def backtest_trade(self) -> r.BacktestTradeRecord:
        return r.BacktestTradeRecord(_id=self._next("btt"), backtest_run_id="bt")

    def system_event(self) -> r.SystemEventRecord:
        return r.SystemEventRecord(_id=self._next("evt"), type="startup", ts=NOW)

    def market_calendar(self) -> r.MarketCalendarRecord:
        return r.MarketCalendarRecord(_id=self._next("cal"), date=self._next("2026-01-05"))

    def kill_switch(self, halted: bool = False) -> r.KillSwitchRecord:
        return r.KillSwitchRecord(_id="kill_switch", halted=halted)

    def command(self) -> r.CommandRecord:
        return r.CommandRecord(
            _id=self._next("cmd"),
            idempotency_key=self._next("idem"),
            type="halt",
            status="PENDING",
            created_at=NOW,
        )

    def command_result(self, command_id: str | None = None) -> r.CommandResultRecord:
        return r.CommandResultRecord(
            _id=self._next("res"), command_id=command_id or self._next("cmd"), created_at=NOW
        )
