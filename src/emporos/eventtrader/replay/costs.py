"""What a replayed round trip costs, behind one Protocol (EM-240). The program's own schedules
implement it in `program_costs`; tests use a flat double."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from emporos.eventtrader.replay.records import Scenario, TradeLeg

__all__ = ["FlatCosts", "TradeCosts"]


class TradeCosts(Protocol):
    def cost(self, leg: TradeLeg, scenario: Scenario) -> Decimal:
        """Rupees: statutory charges, brokerage and slippage on both sides."""
        ...


class FlatCosts:
    """A fraction of the entry turnover, per scenario: for tests and for sanity checks."""

    def __init__(self, benchmark: Decimal, adverse: Decimal) -> None:
        self._rates = {Scenario.BENCHMARK: benchmark, Scenario.ADVERSE: adverse}

    def cost(self, leg: TradeLeg, scenario: Scenario) -> Decimal:
        return leg.entry_price * leg.quantity * self._rates[scenario]
