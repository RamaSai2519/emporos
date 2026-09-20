"""The rule set of plan.md §11, in the plan's order.

System-wide guards come first (cheap, and no point sizing an order the system may not send), then
exposure and loss, then the order's own sanity. Order matters twice: it decides which reason a
rejected signal is recorded with, and it keeps the cheapest checks in front.
"""

from __future__ import annotations

from datetime import timedelta

from emporos.marketdata.session import SessionWindow
from emporos.risk.limits import RiskLimits
from emporos.risk.rules.base import RiskRule
from emporos.risk.rules.exposure import (
    AbnormalSpreadGuard,
    DuplicateOrderGuard,
    MaxCapitalDeployedGuard,
    MaxDailyLossGuard,
    MaxOpenPositionsGuard,
    MaxOrderQuantityGuard,
    MaxPositionValueGuard,
    MaxStrategyLossGuard,
    OrderRateGuard,
    PriceSanityGuard,
)
from emporos.risk.rules.system import (
    BrokerHealthGuard,
    KillSwitchGuard,
    MarketSessionGuard,
    ReconciliationGuard,
    StaleDataGuard,
    TradingModeGuard,
)


class StandardRuleSet:
    def __init__(self, limits: RiskLimits, window: SessionWindow) -> None:
        self._limits = limits
        self._window = window

    def rules(self) -> list[RiskRule]:
        limits = self._limits
        return [
            TradingModeGuard(),
            KillSwitchGuard(),
            MarketSessionGuard(self._window),
            StaleDataGuard(),
            BrokerHealthGuard(),
            ReconciliationGuard(),
            DuplicateOrderGuard(timedelta(seconds=limits.duplicate_window_seconds)),
            MaxDailyLossGuard(limits.max_daily_loss),
            MaxStrategyLossGuard(limits.max_strategy_loss),
            MaxPositionValueGuard(limits.max_position_value),
            MaxOpenPositionsGuard(limits.max_open_positions),
            MaxCapitalDeployedGuard(limits.max_capital_deployed),
            MaxOrderQuantityGuard(limits.max_order_quantity),
            PriceSanityGuard(limits.max_price_deviation_pct),
            AbnormalSpreadGuard(limits.max_spread_bps),
            OrderRateGuard(limits.max_orders_per_second, limits.max_orders_per_minute),
        ]
