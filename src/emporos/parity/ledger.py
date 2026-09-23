"""The comparison's own records: one row per signal, one per round trip, one per session.

Rows hold plain numbers (never a broker or backtest object), so a whole comparison can be
persisted, re-sliced by strategy / symbol / session / trade, and re-aggregated over any period
without going back to the two sides it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from emporos.backtest.portfolio import ClosedTrade, TradeDirection
from emporos.parity.models import ParityStatus, SideOutcome, SignalPoint


@dataclass(frozen=True)
class TradeFacts:
    """One closed round trip, with the amounts the analysis needs."""

    opened_at: datetime
    closed_at: datetime
    quantity: int
    entry_notional: Decimal
    gross_pnl: Decimal
    fees: Decimal

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees

    @classmethod
    def of(cls, trade: ClosedTrade) -> TradeFacts:
        return cls(
            trade.opened_at, trade.closed_at, trade.quantity, trade.entry_notional.amount,
            trade.gross_pnl.amount, trade.fees.amount,
        )  # fmt: skip


@dataclass(frozen=True)
class SignalParity:
    """How one signal fared: on each side, and the single status that names the difference."""

    instrument_id: str
    status: ParityStatus
    reason: str
    paper: SignalPoint | None
    backtest: SignalPoint | None
    paper_outcome: SideOutcome | None
    backtest_outcome: SideOutcome | None


@dataclass(frozen=True)
class TradePair:
    """A round trip on either or both sides. Unpaired trips are results, never dropped."""

    instrument_id: str
    direction: TradeDirection
    paper: TradeFacts | None
    backtest: TradeFacts | None


@dataclass(frozen=True)
class SessionParity:
    strategy: str
    run_id: str
    session_date: date
    config_hash: str
    starting_cash: Decimal
    signals: tuple[SignalParity, ...]
    trades: tuple[TradePair, ...]
