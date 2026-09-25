"""A put credit spread whose strikes come from what is LISTED, and whose worst-case loss is capped
in rupees (EM-226, PROFIT_PLAN.md §3.5 and §5 B1).

The short put is the listed strike nearest to `spot - k standard deviations` at or below it, where
one standard deviation over the days left is `spot x volatility x sqrt(days / 365)`. The long put is
the WIDEST listed strike below the short whose width times the lot size stays within the cap, so the
spread's maximum loss can never exceed the cap before the credit; if no listed strike qualifies the
day is skipped. Only strikes present in the chain are used, never an interpolated one."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.options.chain import ChainSnapshot, OptionRight
from emporos.options.entry import VolatilitySource
from emporos.options.spread import SpreadPlan, Vertical

__all__ = ["RiskCappedPutSpread"]

_YEAR = Decimal(365)


@dataclass(frozen=True)
class RiskCappedPutSpread:
    volatility: VolatilitySource
    sigmas: Decimal
    max_loss_rupees: Decimal

    def __post_init__(self) -> None:
        if self.sigmas <= 0 or self.max_loss_rupees <= 0:
            raise ValueError("sigmas and the loss cap must be positive")

    def build(self, snapshot: ChainSnapshot, expiry: date) -> SpreadPlan | None:
        vol = self.volatility.annual_volatility(snapshot.day)
        chain = snapshot.expiries.get(expiry)
        if vol is None or vol <= 0 or chain is None:
            return None
        spot = snapshot.underlying_close
        deviation = spot * vol * (Decimal(snapshot.days_to(expiry)) / _YEAR).sqrt()
        target = spot - self.sigmas * deviation
        puts = sorted(s for s, right in chain.quotes if right is OptionRight.PUT)
        shorts = [s for s in puts if s <= target]
        if not shorts:
            return None
        short = shorts[-1]
        widest = self.max_loss_rupees / snapshot.lot_size
        longs = [s for s in puts if s < short and short - s <= widest]
        if not longs:
            return None
        return SpreadPlan(expiry, (Vertical(OptionRight.PUT, short, longs[0]),))
