"""The numbers the exposure and sanity rules enforce, as one validated, immutable document.

Every limit is a positive number: a zero or negative cap is a misconfiguration that would either
block everything or (for a loss cap) mean nothing, so it is refused when the file is loaded.
Money is an exact string or integer in the YAML, never a float (plan.md §6).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from emporos.strategies.config import ExactDecimal, PositiveInt


class RiskLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_daily_loss: ExactDecimal = Field(gt=0)  # rupees; blocks new entries once exceeded
    max_strategy_loss: ExactDecimal = Field(gt=0)  # rupees, per strategy run
    max_position_value: ExactDecimal = Field(gt=0)  # rupees, per instrument
    max_open_positions: PositiveInt
    max_capital_deployed: ExactDecimal = Field(gt=0)  # rupees, across all positions
    max_order_quantity: PositiveInt  # shares in one order (fat-finger)
    max_price_deviation_pct: ExactDecimal = Field(gt=0, lt=100)  # limit price vs last price
    max_spread_bps: ExactDecimal = Field(gt=0)  # widest bid/ask spread an entry tolerates
    duplicate_window_seconds: PositiveInt
    max_orders_per_second: PositiveInt
    max_orders_per_minute: PositiveInt
