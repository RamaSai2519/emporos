"""The price execution actually sends: marketable, on the tick grid, never a market order."""

from __future__ import annotations

from emporos.domain.marketable import MarketableLimit
from emporos.domain.money import Money
from emporos.execution.errors import InvalidOrderPriceError
from emporos.execution.ports import TickSizes
from emporos.risk.approval import RiskApprovedSignal


class MarketableLimitPricer:
    """Widen a signal's limit through the market by a buffer, then require a legal price.

    A replacement order is priced by the repricing policy already (`marketable=False`): buffering it
    again would push it past the chase bound the policy just enforced.
    """

    def __init__(self, rule: MarketableLimit, ticks: TickSizes) -> None:
        self._rule = rule
        self._ticks = ticks

    async def price(
        self, approval: RiskApprovedSignal, *, marketable: bool
    ) -> tuple[Money, Money | None]:
        signal = approval.signal
        tick = await self._ticks.tick_size(signal.instrument_id)
        if marketable:
            prices = self._rule.prices(signal, tick)
            limit, trigger = prices.limit, prices.trigger
        else:
            limit, trigger = signal.limit_price, signal.trigger_price
        for price in (limit, trigger):
            self._require_legal(signal.instrument_id, price, tick)
        return limit, trigger

    @staticmethod
    def _require_legal(instrument_id: str, price: Money | None, tick: Money) -> None:
        if price is None:
            return
        if price <= Money.zero() or price.amount % tick.amount != 0:
            raise InvalidOrderPriceError(
                f"{price.amount} is not a positive multiple of {instrument_id} tick {tick.amount}"
            )
