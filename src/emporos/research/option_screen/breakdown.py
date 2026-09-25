"""Where the credit went (EM-234): one backtest run reduced to the per-spread figures that decide
whether a put-spread structure can pay for its own fees.

Per spread, in rupees: the credit received, the gross P&L before statutory charges and brokerage
(slippage is inside it), the charges, the net, the win rate and the average win and loss. `charges`
are statutory charges plus brokerage only. Money stays `Decimal` until the end; the results are
reporting figures, so they are floats."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from emporos.options.backtest import BacktestResult

__all__ = ["CostBreakdown"]


@dataclass(frozen=True)
class CostBreakdown:
    trips: int
    credit: float  # mean credit received per spread
    gross: float  # mean gross P&L per spread (fills slippage included, charges excluded)
    charges: float  # mean brokerage and statutory charges per spread
    net: float
    win_rate: float  # net of charges
    average_win: float | None
    average_loss: float | None
    worst_spread: float  # the largest net loss of any single spread (negative), 0 with no loss
    max_loss: float  # the largest worst-case loss any spread was opened with
    exits: dict[str, int]

    @property
    def charges_share_of_credit(self) -> float | None:
        return self.charges / self.credit if self.credit > 0 else None

    @property
    def gross_to_charges(self) -> float | None:
        return self.gross / self.charges if self.charges > 0 else None

    @classmethod
    def of(cls, result: BacktestResult) -> CostBreakdown:
        trades = result.trades
        if not trades:
            return cls(0, 0.0, 0.0, 0.0, 0.0, 0.0, None, None, 0.0, 0.0, {})
        n = len(trades)
        nets = [t.net_pnl for t in trades]
        wins = [v for v in nets if v > 0]
        losses = [v for v in nets if v <= 0]
        return cls(
            trips=n,
            credit=_mean([t.credit_per_unit * t.units for t in trades]),
            gross=_mean([t.gross_pnl for t in trades]),
            charges=_mean([t.charges for t in trades]),
            net=_mean(nets),
            win_rate=len(wins) / n,
            average_win=_mean(wins) if wins else None,
            average_loss=_mean(losses) if losses else None,
            worst_spread=float(min(min(nets), Decimal(0))),
            max_loss=float(max(t.max_loss for t in trades)),
            exits=dict(Counter(t.reason.value for t in trades)),
        )


def _mean(values: list[Decimal]) -> float:
    return float(sum(values, Decimal(0)) / len(values))
