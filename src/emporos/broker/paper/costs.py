"""What a trade costs. The paper broker takes a `CostModel`; it invents no rates itself."""

from __future__ import annotations

from typing import Protocol

from emporos.broker.models import BrokerTrade
from emporos.domain.money import Money


class CostModel(Protocol):
    def charges(self, trade: BrokerTrade) -> Money:
        """Total brokerage, taxes and levies for this one trade, as a non-negative amount."""
        ...


class NoCosts:
    """Trades are free. Only for tests that are not about P&L — realistic P&L needs a schedule."""

    def charges(self, trade: BrokerTrade) -> Money:
        return Money.zero()
