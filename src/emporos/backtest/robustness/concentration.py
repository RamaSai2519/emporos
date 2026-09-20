"""Is a profit spread across instruments, months and trades, or is it a few lucky ones?

Each share is the best bucket's net P&L over the run's total net P&L: 0.5 means one instrument (or
month, or the top few trades) made half of everything. A share can exceed 1 when the rest lost
money. A run that did not make a profit has no shares: "which part of the profit" has no answer.
Months are IST calendar months of the trade's close.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.portfolio import ClosedTrade
from emporos.core.clock import IST


@dataclass(frozen=True)
class ConcentrationReport:
    net_pnl: Decimal
    top_instrument: str | None
    top_instrument_share: Decimal | None
    top_month: str | None
    top_month_share: Decimal | None
    top_trades: int
    top_trades_share: Decimal | None

    @property
    def defined(self) -> bool:
        return self.top_instrument_share is not None


class ConcentrationCheck:
    def __init__(self, top_trades: int) -> None:
        if top_trades < 1:
            raise ValueError("top_trades must be at least 1")
        self._top = top_trades

    def measure(self, trades: Sequence[ClosedTrade]) -> ConcentrationReport:
        total = sum((t.net_pnl.amount for t in trades), ZERO)
        if total <= ZERO:
            return ConcentrationReport(total, None, None, None, None, self._top, None)
        instrument, instrument_net = self._best(trades, lambda t: t.instrument_id)
        month, month_net = self._best(trades, lambda t: f"{t.closed_at.astimezone(IST):%Y-%m}")
        best_trades = sum(
            sorted((t.net_pnl.amount for t in trades), reverse=True)[: self._top], ZERO
        )
        return ConcentrationReport(
            total,
            instrument,
            DecimalMath.divide(instrument_net, total),
            month,
            DecimalMath.divide(month_net, total),
            self._top,
            DecimalMath.divide(best_trades, total),
        )

    @staticmethod
    def _best(trades: Sequence[ClosedTrade], bucket) -> tuple[str, Decimal]:  # type: ignore[no-untyped-def]
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for trade in trades:
            totals[bucket(trade)] += trade.net_pnl.amount
        name = max(sorted(totals), key=lambda k: totals[k])  # ties go to the earlier name
        return name, totals[name]
