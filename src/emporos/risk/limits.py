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
    # Worst-case loss of one entry, |limit - stop| x quantity (EM-189). Optional: unset means the
    # guard is not registered, so only a tier that names it (live conservative) enforces it.
    max_risk_per_trade: ExactDecimal | None = Field(default=None, gt=0)

    # The account the caps are sized against. Optional so a limits document that does not know its
    # account still loads, but when it is stated every cap is checked against it (EM-189): a cap
    # above the money that exists is not a cap.
    account_capital: ExactDecimal | None = Field(default=None, gt=0)

    def consistency_problems(self) -> list[str]:
        """Caps that contradict each other. Checked when a limits FILE loads (EM-189), not when
        an in-memory value is built, so a test can still construct a deliberately extreme one."""
        problems: list[str] = []
        if self.max_position_value > self.max_capital_deployed:
            problems.append("max_position_value must not exceed max_capital_deployed")
        if self.account_capital is not None:
            if self.max_capital_deployed > self.account_capital:
                problems.append("max_capital_deployed must not exceed account_capital")
            if self.max_daily_loss > self.account_capital:
                problems.append("max_daily_loss must not exceed account_capital")
        return problems

    def is_within(self, ceiling: RiskLimits) -> bool:
        """True when no cap here is looser than the same cap in `ceiling` (a stricter tier)."""
        if ceiling.max_risk_per_trade is not None and (
            self.max_risk_per_trade is None or self.max_risk_per_trade > ceiling.max_risk_per_trade
        ):
            return False
        return (
            self.max_daily_loss <= ceiling.max_daily_loss
            and self.max_strategy_loss <= ceiling.max_strategy_loss
            and self.max_position_value <= ceiling.max_position_value
            and self.max_open_positions <= ceiling.max_open_positions
            and self.max_capital_deployed <= ceiling.max_capital_deployed
            and self.max_order_quantity <= ceiling.max_order_quantity
            and self.max_price_deviation_pct <= ceiling.max_price_deviation_pct
            and self.max_spread_bps <= ceiling.max_spread_bps
            and self.max_orders_per_second <= ceiling.max_orders_per_second
            and self.max_orders_per_minute <= ceiling.max_orders_per_minute
            and self.duplicate_window_seconds >= ceiling.duplicate_window_seconds
        )
