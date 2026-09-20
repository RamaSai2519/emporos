"""How a bar fills a resting limit order (plan.md §10 "Fill model").

Small policies, injected: WHEN does a bar fill a limit (`LimitCrossing`), at WHAT price
(`FillPrice`), and for HOW MUCH (`BarLiquidity`). Defaults are conservative, because an optimistic
fill model is the most common way a backtest lies:

* `ThroughBar` — the bar must trade STRICTLY through the limit (a buy needs low < limit). A bar
  that merely touches the limit does not fill it: we cannot know we were at the front of the queue.
  `TouchBar` (low <= limit) is one constructor argument away. This is the backtest's own default;
  the paper broker's default (touch) is a separate, still-open operator decision (EM-99 G3).
* `AtLimitPrice` — filled at the order's own limit; a limit order never gets a price improvement.
* `BarParticipation(10%)` — at most a tenth of the bar's volume; a bar with no volume fills nothing.
* a `partial` bar (the feed had a gap inside it) fills nothing: known-incomplete data.

Stop triggers use `TouchTrigger`: a stop is triggered when the bar's range reaches the trigger.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_FLOOR, ROUND_UP, Decimal
from typing import Protocol

from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide

_BPS = Decimal(10_000)
_PAISA = Decimal("0.01")


class LimitCrossing(Protocol):
    def crossed(self, side: OrderSide, limit: Money, bar: Candle) -> bool: ...


class ThroughBar:
    """Strictly through: a buy needs the bar's low below the limit, a sell its high above."""

    def crossed(self, side: OrderSide, limit: Money, bar: Candle) -> bool:
        return bar.low < limit if side is OrderSide.BUY else bar.high > limit


class TouchBar:
    """At or through: optimistic about queue position; opt in deliberately."""

    def crossed(self, side: OrderSide, limit: Money, bar: Candle) -> bool:
        return bar.low <= limit if side is OrderSide.BUY else bar.high >= limit


class TriggerRule(Protocol):
    def triggered(self, side: OrderSide, trigger: Money, bar: Candle) -> bool: ...


class TouchTrigger:
    """A buy stop triggers when the bar's high reaches the trigger, a sell stop on its low."""

    def triggered(self, side: OrderSide, trigger: Money, bar: Candle) -> bool:
        return bar.high >= trigger if side is OrderSide.BUY else bar.low <= trigger


class FillPrice(Protocol):
    def at(self, side: OrderSide, limit: Money, bar: Candle) -> Money:
        """The price of a fill the crossing rule has already allowed. Never worse than `limit`
        for the order, never better than the model claims."""
        ...


class AtLimitPrice:
    def at(self, side: OrderSide, limit: Money, bar: Candle) -> Money:
        return limit


class GapAwareSlippage:
    """Fills at the bar's open when that is better than the limit (a gap), moved AGAINST the order
    by `slippage_bps`, and never past the limit. The paisa rounding is against the order too."""

    def __init__(self, slippage_bps: Decimal) -> None:
        if slippage_bps < 0:
            raise ValueError("slippage cannot be negative")
        self._bps = slippage_bps

    def at(self, side: OrderSide, limit: Money, bar: Candle) -> Money:
        if side is OrderSide.BUY:
            reference = min(limit, bar.open).amount
            moved = (reference * (1 + self._bps / _BPS)).quantize(_PAISA, rounding=ROUND_UP)
            return Money(min(limit.amount, moved))
        reference = max(limit, bar.open).amount
        moved = (reference * (1 - self._bps / _BPS)).quantize(_PAISA, rounding=ROUND_DOWN)
        return Money(max(limit.amount, moved))


class BarLiquidity(Protocol):
    def capacity(self, bar: Candle) -> int:
        """Shares of this bar that our orders, together, may take."""
        ...


class BarParticipation:
    def __init__(self, fraction: Decimal) -> None:
        if not Decimal(0) < fraction <= Decimal(1):
            raise ValueError("participation must be in (0, 1]")
        self._fraction = fraction

    def capacity(self, bar: Candle) -> int:
        return int((bar.volume * self._fraction).to_integral_value(ROUND_FLOOR))


class BarFillModel:
    """The three policies together: what a bar does to a resting order."""

    def __init__(
        self,
        crossing: LimitCrossing | None = None,
        price: FillPrice | None = None,
        liquidity: BarLiquidity | None = None,
        trigger: TriggerRule | None = None,
    ) -> None:
        self._crossing = crossing or ThroughBar()
        self._price = price or AtLimitPrice()
        self._liquidity = liquidity or BarParticipation(Decimal("0.1"))
        self._trigger = trigger or TouchTrigger()

    def capacity(self, bar: Candle) -> int:
        return 0 if bar.partial else self._liquidity.capacity(bar)

    def price_if_filled(self, side: OrderSide, limit: Money, bar: Candle) -> Money | None:
        """The fill price, or None when the bar does not trade through the limit."""
        if bar.partial or not self._crossing.crossed(side, limit, bar):
            return None
        return self._price.at(side, limit, bar)

    def triggers(self, side: OrderSide, trigger: Money, bar: Candle) -> bool:
        return not bar.partial and self._trigger.triggered(side, trigger, bar)
