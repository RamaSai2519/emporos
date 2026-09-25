"""What a delivery fill costs: the dated fee schedule and a slippage assumption (EM-223).

PROFIT_PLAN §3.1 fixes two scenarios for Track A: BENCHMARK (fees as scheduled, 10 bps of slippage
on each side) and ADVERSE (fees x1.5, 25 bps on each side). Slippage moves the fill price against
the trader (a buy fills higher, a sell lower); the fee is the schedule's, on the fill's turnover.
Neither can be loosened here: a caller may only pick one of these, or build its own scenario and
be reported as having done so.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from emporos.domain.fees import DeliveryCharges, FeeSchedule
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

__all__ = ["ADVERSE", "BENCHMARK", "CostScenario", "SwingCostModel"]

_BPS = Decimal(10_000)


@dataclass(frozen=True)
class CostScenario:
    name: str
    fee_multiple: Decimal
    slippage_bps: Decimal  # per side

    def __post_init__(self) -> None:
        if self.fee_multiple < 1:
            raise ValueError("a fee multiple below 1 would loosen the schedule")
        if self.slippage_bps < 0:
            raise ValueError("slippage cannot be negative")


BENCHMARK = CostScenario("benchmark", Decimal(1), Decimal(10))
ADVERSE = CostScenario("adverse", Decimal("1.5"), Decimal(25))


class SwingCostModel:
    def __init__(self, schedule: FeeSchedule, scenario: CostScenario) -> None:
        self._charges = DeliveryCharges(schedule)
        self._scenario = scenario

    @property
    def scenario(self) -> CostScenario:
        return self._scenario

    def buy_price(self, raw_price: Decimal) -> Decimal:
        return raw_price * (1 + self._scenario.slippage_bps / _BPS)

    def sell_price(self, raw_price: Decimal) -> Decimal:
        return raw_price * (1 - self._scenario.slippage_bps / _BPS)

    def fees(self, side: OrderSide, quantity: int, fill_price: Decimal) -> Decimal:
        """The scenario's fees for an order of `quantity` whole shares filled at `fill_price`."""
        charges = self._charges.for_trade(Exchange.NSE, side, quantity, Money(fill_price))
        return charges.total.amount * self._scenario.fee_multiple
