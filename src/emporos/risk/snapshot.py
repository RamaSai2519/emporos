"""The immutable picture of the world a risk rule judges a signal against (plan.md §11).

Rules are pure functions of `(signal, snapshot)`. Everything a rule may need is in here, so a
rejection can be replayed later from the persisted snapshot alone. Every default is FAIL-SAFE: a
snapshot built with nothing filled in blocks live trading, reads as stale, and has no reconciled
state — an unwired source can only ever make the engine stricter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeVar

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from emporos.domain.trading_mode import TradingMode

_V = TypeVar("_V")


def _frozen_map(values: Mapping[str, _V] | None) -> Mapping[str, _V]:
    return MappingProxyType(dict(values or {}))


class ReconciliationStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    FAILED = "FAILED"


@dataclass(frozen=True)
class KillSwitchReading:
    """`known` is False until the switch has actually been read: unknown is treated as halted."""

    halted: bool = True
    known: bool = False
    source: str = "unread"
    reason: str = ""


@dataclass(frozen=True)
class SystemFacts:
    mode: TradingMode = TradingMode.LIVE
    live_trading_enabled: bool = False
    kill_switch: KillSwitchReading = field(default_factory=KillSwitchReading)
    broker_session_ok: bool = False
    order_feed_ok: bool = False
    reconciliation: ReconciliationStatus = ReconciliationStatus.PENDING


@dataclass(frozen=True)
class InstrumentMarket:
    """What is known about one instrument's market right now. `stale` defaults to True."""

    ltp: Money | None = None
    bid: Money | None = None
    ask: Money | None = None
    lower_circuit: Money | None = None
    upper_circuit: Money | None = None
    stale: bool = True


@dataclass(frozen=True)
class WorkingOrder:
    """An order that is live (or unresolved) at the broker."""

    instrument_id: str
    side: OrderSide
    placed_at: datetime

    def __post_init__(self) -> None:
        if self.placed_at.tzinfo is None or self.placed_at.utcoffset() != UTC.utcoffset(None):
            raise ValueError("placed_at must be timezone-aware UTC")


@dataclass(frozen=True)
class AccountFacts:
    positions: Mapping[str, Position] = field(default_factory=lambda: _frozen_map(None))
    daily_pnl: Money = field(default_factory=Money.zero)  # realised + unrealised, after charges
    strategy_pnl: Mapping[str, Money] = field(default_factory=lambda: _frozen_map(None))

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", _frozen_map(self.positions))
        object.__setattr__(self, "strategy_pnl", _frozen_map(self.strategy_pnl))

    def position(self, instrument_id: str) -> Position:
        return self.positions.get(instrument_id) or Position.flat(instrument_id)

    @property
    def open_position_count(self) -> int:
        return sum(1 for p in self.positions.values() if not p.is_flat)

    @property
    def capital_deployed(self) -> Money:
        total = Money.zero()
        for position in self.positions.values():
            total = total + position.average_price.times(abs(position.net_quantity))
        return total


@dataclass(frozen=True)
class OrderFlowFacts:
    working: tuple[WorkingOrder, ...] = ()
    recent_order_times: tuple[datetime, ...] = ()  # when orders were sent, for the OPS budget


@dataclass(frozen=True)
class RiskSnapshot:
    now: datetime
    system: SystemFacts = field(default_factory=SystemFacts)
    account: AccountFacts = field(default_factory=AccountFacts)
    flow: OrderFlowFacts = field(default_factory=OrderFlowFacts)
    markets: Mapping[str, InstrumentMarket] = field(default_factory=lambda: _frozen_map(None))

    def __post_init__(self) -> None:
        if self.now.tzinfo is None or self.now.utcoffset() != UTC.utcoffset(None):
            raise ValueError("snapshot time must be timezone-aware UTC")
        object.__setattr__(self, "markets", _frozen_map(self.markets))

    def market(self, instrument_id: str) -> InstrumentMarket:
        return self.markets.get(instrument_id) or InstrumentMarket()

    def describe(self) -> dict[str, object]:
        """A JSON-safe rendering (money as exact strings) stored with every rejection."""
        return {
            "now": self.now.isoformat(),
            "system": {
                "mode": self.system.mode.value,
                "live_trading_enabled": self.system.live_trading_enabled,
                "kill_switch": {
                    "halted": self.system.kill_switch.halted,
                    "known": self.system.kill_switch.known,
                    "source": self.system.kill_switch.source,
                    "reason": self.system.kill_switch.reason,
                },
                "broker_session_ok": self.system.broker_session_ok,
                "order_feed_ok": self.system.order_feed_ok,
                "reconciliation": self.system.reconciliation.value,
            },
            "account": {
                "daily_pnl": str(self.account.daily_pnl.amount),
                "open_positions": self.account.open_position_count,
                "capital_deployed": str(self.account.capital_deployed.amount),
                "positions": {
                    key: {
                        "net_quantity": p.net_quantity,
                        "average_price": str(p.average_price.amount),
                    }
                    for key, p in self.account.positions.items()
                },
                "strategy_pnl": {k: str(v.amount) for k, v in self.account.strategy_pnl.items()},
            },
            "flow": {
                "working": [
                    {
                        "instrument_id": w.instrument_id,
                        "side": w.side.value,
                        "placed_at": w.placed_at.isoformat(),
                    }
                    for w in self.flow.working
                ],
                "orders_sent_recently": len(self.flow.recent_order_times),
            },
            "markets": {key: self._describe_market(m) for key, m in self.markets.items()},
        }

    @staticmethod
    def _describe_market(market: InstrumentMarket) -> dict[str, object]:
        def text(value: Money | None) -> str | None:
            return None if value is None else str(value.amount)

        return {
            "ltp": text(market.ltp),
            "bid": text(market.bid),
            "ask": text(market.ask),
            "lower_circuit": text(market.lower_circuit),
            "upper_circuit": text(market.upper_circuit),
            "stale": market.stale,
        }
