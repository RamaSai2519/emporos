"""Sizing and the rule list, as one object for the backtest, paper and (later) live (EM-240)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from emporos.eventtrader.risk.limits import RiskLimits
from emporos.eventtrader.risk.models import (
    EntryProposal,
    Product,
    Refusal,
    RiskDecision,
    RiskSnapshot,
    Sized,
)
from emporos.eventtrader.risk.rules import RiskRule, TotalLossKill, floor_units, standard_rules

__all__ = ["RiskEngine"]


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None, rules: Sequence[RiskRule] | None = None):
        self._limits = limits or RiskLimits()
        self._rules = list(rules if rules is not None else standard_rules(self._limits))
        names = [r.name for r in self._rules]
        if not names or len(names) != len(set(names)):
            raise ValueError("a risk engine needs rules, each with its own name")
        self._kill = TotalLossKill(self._limits)

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    def kill_tripped(self, snapshot: RiskSnapshot) -> bool:
        """True once Rs 25,000 is lost: the runner closes everything and the track stops."""
        return self._kill.tripped(snapshot)

    def review(self, proposal: EntryProposal, snapshot: RiskSnapshot) -> RiskDecision:
        """Size the proposal from its stop, then run every rule. Approved only if none refuses;
        every refusal is returned, so a log shows the whole picture."""
        sized = self.size(proposal, snapshot)
        if isinstance(sized, Refusal):
            return RiskDecision(False, None, (sized,))
        refusals = tuple(
            r for rule in self._rules if (r := rule.check(proposal, sized, snapshot)) is not None
        )
        return RiskDecision(False, None, refusals) if refusals else RiskDecision(True, sized)

    def size(self, p: EntryProposal, snapshot: RiskSnapshot) -> Sized | Refusal:
        """Cash: floor(risk budget / |entry - stop|), then capped by the position value. An
        option: whole lots whose premium fits the premium budget. The budget is scaled by the
        day's posture (never above 1)."""
        scale = min(snapshot.posture_scale, Decimal(1))
        if scale <= 0:
            return Refusal("posture_hold", "today's posture is hold")
        if p.product is Product.OPTION:
            return self._size_option(p, scale)
        if p.stop_price is None or p.stop_price == p.entry_price:
            return Refusal("sizing", "a cash entry needs a stop away from the entry")
        distance = abs(p.entry_price - p.stop_price)
        cap = (
            self._limits.intraday_position_value
            if p.product is Product.INTRADAY
            else self._limits.swing_position_value
        )
        quantity = min(
            floor_units(self._limits.cash_risk_per_trade * scale, distance),
            floor_units(cap, p.entry_price),
        )
        if quantity < 1:
            return Refusal("sizing", "the stop is too wide for one share within the budgets")
        return Sized(quantity, p.entry_price * quantity, distance * quantity)

    def _size_option(self, p: EntryProposal, scale: Decimal) -> Sized | Refusal:
        per_lot = p.entry_price * p.lot_size
        lots = floor_units(self._limits.option_premium_per_trade * scale, per_lot)
        if lots < 1:
            return Refusal("sizing", f"one lot costs Rs {per_lot}, over the premium budget")
        units = lots * p.lot_size
        return Sized(units, p.entry_price * units, p.entry_price * units)
