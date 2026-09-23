"""A minimal transaction-cost adjustment for feature-level expectancy (EM-178).

This is NOT the portfolio cost model — EM-183 owns that, and will need a per-strategy, tiered
market-impact model beyond what a single feature study needs. Here, one round trip (open then
close) pays the SAME statutory charges a live order would (`IntradayCharges`, reusing the dated
fee schedule so nothing here invents its own rates) plus a configurable slippage assumption in
basis points, charged on both legs — conservative and explicit, never hidden inside an unstated
"friction constant".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.domain.fees import FeeSchedule, IntradayCharges
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

_BASIS_POINTS = Decimal(10_000)


@dataclass(frozen=True)
class TransactionCostModel:
    schedule: FeeSchedule
    slippage_bps: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.slippage_bps < 0:
            raise ValueError("slippage cannot be negative")

    @property
    def label(self) -> str:
        return f"{self.schedule.name}+{self.slippage_bps}bps"

    def round_trip_fraction(self, exchange: Exchange, quantity: int, price: Money) -> Decimal:
        """Statutory charges for a buy and a sell at `price`, plus slippage on both legs, as a
        fraction of the position's notional value."""
        charges = IntradayCharges(self.schedule)
        buy = charges.for_trade(exchange, OrderSide.BUY, quantity, price).total
        sell = charges.for_trade(exchange, OrderSide.SELL, quantity, price).total
        notional = price.amount * quantity
        statutory = DecimalMath.divide(buy.amount + sell.amount, notional)
        slippage = DecimalMath.divide(self.slippage_bps * 2, _BASIS_POINTS)
        return statutory + slippage

    def adjusted_return(
        self, gross_return: Decimal, exchange: Exchange, quantity: int, price: Money
    ) -> Decimal:
        return gross_return - self.round_trip_fraction(exchange, quantity, price)
