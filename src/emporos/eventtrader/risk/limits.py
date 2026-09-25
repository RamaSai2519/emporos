"""The declared limits (PROFIT_PLAN §12.4). They are values, in one place, so the backtest, paper
and live all read the same numbers and a test can pin them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal

__all__ = ["RiskLimits"]


@dataclass(frozen=True)
class RiskLimits:
    cash_risk_per_trade: Decimal = Decimal(2000)
    option_premium_per_trade: Decimal = Decimal(5000)
    daily_loss: Decimal = Decimal(5000)
    total_loss: Decimal = Decimal(25000)
    open_risk: Decimal = Decimal(25000)  # 25% of the Rs 1,00,000 capital
    intraday_position_value: Decimal = Decimal(50000)
    swing_position_value: Decimal = Decimal(25000)
    intraday_stop_pct: Decimal = Decimal(5)
    swing_stop_pct: Decimal = Decimal(12)
    max_intraday_positions: int = 3
    max_swing_positions: int = 4  # options count as swing
    no_intraday_entry_after: time = time(14, 45)
    square_off_at: time = time(15, 15)
    session_open: time = time(9, 15)
