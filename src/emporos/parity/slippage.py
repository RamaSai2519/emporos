"""Slippage against the price the system saw when it decided.

The reference is the decision quote's mid when both sides of the book were recorded, else its
last traded price, else the signal's own price (a backtest has no quote, and older paper rows
have none). The reference kind travels with the number, so a report never silently mixes
mid-based paper slippage with signal-price-based backtest slippage.
"""

from __future__ import annotations

from decimal import Decimal

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.parity.models import ReferenceKind, Slippage
from emporos.signals.quotes import DecisionQuote

_BPS = Decimal(10_000)
_TWO = Decimal(2)


class SlippageMeasure:
    def reference(
        self, quote: DecisionQuote | None, signal_price: Money
    ) -> tuple[Money, ReferenceKind]:
        if quote is not None:
            if quote.bid is not None and quote.ask is not None:
                return Money((quote.bid.amount + quote.ask.amount) / _TWO), ReferenceKind.MID
            if quote.ltp is not None:
                return quote.ltp, ReferenceKind.LTP
        return signal_price, ReferenceKind.SIGNAL_PRICE

    def measure(
        self, side: OrderSide, fill_price: Money, quote: DecisionQuote | None, signal_price: Money
    ) -> Slippage:
        reference, kind = self.reference(quote, signal_price)
        if reference.amount <= 0:
            raise ValueError("a slippage reference price must be positive")
        signed = fill_price.amount - reference.amount
        if side is OrderSide.SELL:
            signed = -signed
        return Slippage(signed / reference.amount * _BPS, reference, kind)
