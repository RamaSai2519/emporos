"""What a trade costs. The paper broker takes a `CostModel`; it invents no rates itself."""

from __future__ import annotations

from typing import Protocol

from emporos.broker.models import BrokerTrade
from emporos.domain.fees import IntradayCharges
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money


class CostModel(Protocol):
    def charges(self, trade: BrokerTrade) -> Money:
        """Total brokerage, taxes and levies for this one trade, as a non-negative amount."""
        ...


class NoCosts:
    """Trades are free. Only for tests that are not about P&L — realistic P&L needs a schedule."""

    def charges(self, trade: BrokerTrade) -> Money:
        return Money.zero()


class ScheduledCosts:
    """Charges from a dated fee schedule: brokerage, STT, exchange and SEBI fees, stamp duty, GST.

    The paper broker simulates cash INTRADAY only, so the intraday tariff is the right one."""

    def __init__(self, charges: IntradayCharges) -> None:
        self._charges = charges

    def charges(self, trade: BrokerTrade) -> Money:
        exchange = Exchange(trade.instrument_id.split(":", 1)[0])
        return self._charges.for_trade(exchange, trade.side, trade.quantity, trade.price).total
