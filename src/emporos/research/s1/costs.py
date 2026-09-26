"""Round-trip costs as a curve of the notional, read fast (EM-219 S1).

The exact model (`IntradayCosts`, benchmark and adverse scenarios) is called on probe round trips of
a grid of notionals, at the entry price on both sides, and the cost FRACTION is interpolated in
log-notional between the probes. A run makes millions of trades, most of them the random-entry
control's; the curve costs the same in every arm and in the control, so they are compared on one
basis. A test bounds its error against the exact model on real legs (exit away from entry)."""

from __future__ import annotations

import math
from bisect import bisect_left
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import IST
from emporos.eventtrader.replay.records import Scenario, TradeLeg
from emporos.eventtrader.risk.models import Product
from emporos.eventtrader.stages.models import Instrument, Side

__all__ = ["CostCurve", "ExactCosts", "PROBE_NOTIONALS"]

PROBE_NOTIONALS = tuple(round(1_000 * 1.35**i) for i in range(0, 25))  # Rs 1,000 to about Rs 1.4m
PROBE_PRICE = Decimal(500)
_WHEN = datetime(2024, 6, 3, 10, 0, tzinfo=IST)


class ExactCosts(Protocol):
    def cost(self, leg: TradeLeg, scenario: Scenario) -> Decimal: ...


class CostCurve:
    def __init__(self, exact: ExactCosts, side: Side) -> None:
        self._xs = [math.log(n) for n in PROBE_NOTIONALS]
        self._fractions = {
            scenario: [self._probe(exact, side, n, scenario) for n in PROBE_NOTIONALS]
            for scenario in Scenario
        }

    @staticmethod
    def _probe(exact: ExactCosts, side: Side, notional: int, scenario: Scenario) -> float:
        quantity = max(1, int(Decimal(notional) / PROBE_PRICE))
        leg = TradeLeg(
            "NSE:1", Instrument.CASH_INTRADAY, Product.INTRADAY, side, quantity, _WHEN,
            PROBE_PRICE, _WHEN, PROBE_PRICE,
        )  # fmt: skip
        return float(exact.cost(leg, scenario)) / float(quantity * PROBE_PRICE)

    def fraction(self, notional: float, scenario: Scenario) -> float:
        """The round-trip cost as a fraction of the notional."""
        fractions = self._fractions[scenario]
        x = math.log(max(notional, 1.0))
        i = bisect_left(self._xs, x)
        if i <= 0:
            return fractions[0]
        if i >= len(self._xs):
            return fractions[-1]
        span = self._xs[i] - self._xs[i - 1]
        weight = (x - self._xs[i - 1]) / span
        return fractions[i - 1] * (1 - weight) + fractions[i] * weight

    def cost(self, notional: float, scenario: Scenario) -> float:
        return notional * self.fraction(notional, scenario)
