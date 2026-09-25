"""What a replayed trade is, once its whole path is known (EM-240)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from emporos.eventtrader.replay.fills import ExitReason
from emporos.eventtrader.risk.models import Product
from emporos.eventtrader.stages.models import Instrument, Side

__all__ = ["Scenario", "TradeLeg", "TradeRecord"]


class Scenario(StrEnum):
    BENCHMARK = "benchmark"
    ADVERSE = "adverse"


@dataclass(frozen=True)
class TradeLeg:
    """The facts a cost model needs about a round trip."""

    name: str
    instrument: Instrument
    product: Product
    side: Side
    quantity: int
    entry_ts: datetime
    entry_price: Decimal
    exit_ts: datetime
    exit_price: Decimal


@dataclass(frozen=True)
class TradeRecord:
    """One trade from entry to exit, with what it cost. Its P&L is known at entry in a replay, so
    the book knows when it is open and when it is realised."""

    event_id: str
    name: str  # the instrument id
    symbol: str
    instrument: Instrument
    product: Product
    side: Side
    quantity: int
    entry_ts: datetime
    entry_price: Decimal
    exit_ts: datetime
    exit_price: Decimal
    exit_reason: ExitReason
    stop_price: Decimal | None
    risk: Decimal  # rupees at the stop (an option: the premium)
    gross_pnl: Decimal
    cost_benchmark: Decimal
    cost_adverse: Decimal
    token_cost_inr: Decimal = Decimal(0)  # what the calls that made this decision cost

    @property
    def net_benchmark(self) -> Decimal:
        return self.gross_pnl - self.cost_benchmark

    @property
    def net_adverse(self) -> Decimal:
        return self.gross_pnl - self.cost_adverse

    def net(self, scenario: Scenario) -> Decimal:
        return self.net_benchmark if scenario is Scenario.BENCHMARK else self.net_adverse
