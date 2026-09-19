"""When a resting limit order trades, at what price, and how many shares (plan.md §10).

Fills are decided from the tick stream and nothing else. Three small injected policies keep the
assumptions explicit and swappable:

* `FillPolicy`      — does this tick trade the order, and at what price?
* `LiquidityModel`  — how many shares can we take from this tick (partial fills, queue position)?
* `TriggerRule`     — when does a stop-loss order's trigger fire?

The defaults are chosen to be conservative, and each assumption is stated where it is made. What
this does NOT model: queue position behind other orders at our price, market impact, hidden
liquidity, or the exchange's own matching of crossing orders. A fill here says "the tape traded
where our order would have been eligible", not "the exchange would certainly have filled us".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import floor
from typing import Protocol

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.ticks import Tick

# +1 for a buyer (who prefers lower prices), -1 for a seller (who prefers higher ones).
_DIRECTION = {OrderSide.BUY: 1, OrderSide.SELL: -1}


def _edge(side: OrderSide, limit: Money, price: Money) -> Decimal:
    """How far `price` is inside the limit for this side: positive = better than the limit for
    the order's owner (a lower price for a buyer, a higher one for a seller)."""
    return (limit.amount - price.amount) * _DIRECTION[side]


@dataclass(frozen=True)
class WorkingOrder:
    """What the policies may know about an order that is resting on the simulated exchange."""

    side: OrderSide
    limit_price: Money
    remaining: int
    trigger_price: Money | None = None


# ---- price: does the tape trade the order, and where ---------------------------------------


class PriceCrossing(Protocol):
    def crosses(self, order: WorkingOrder, price: Money) -> bool: ...


class TouchCrossing:
    """A tick AT the limit price is enough (a buy fills at or below its limit, a sell at or
    above). Optimistic about queue position: at our price we would usually be behind others."""

    def crosses(self, order: WorkingOrder, price: Money) -> bool:
        return _edge(order.side, order.limit_price, price) >= 0


class ThroughCrossing:
    """Only a tick strictly THROUGH the limit fills it — the pessimistic queue assumption: price
    trading at our level does not fill us, trading beyond it certainly does."""

    def crosses(self, order: WorkingOrder, price: Money) -> bool:
        return _edge(order.side, order.limit_price, price) > 0


class FillPricing(Protocol):
    def price(self, order: WorkingOrder, trade_price: Money) -> Money: ...


class AtLimitPrice:
    """Fill at the order's own limit price, never better: a resting order gets its price, and the
    conservative model grants no price improvement."""

    def price(self, order: WorkingOrder, trade_price: Money) -> Money:
        return order.limit_price


class AtTradePrice:
    """Fill at the price the tape traded, moved against the order by `adverse` (slippage).

    A limit order can never fill worse than its limit, so the result is clamped to the limit:
    slippage can erase price improvement but cannot breach the limit."""

    def __init__(self, adverse: Money | None = None) -> None:
        adverse = adverse or Money.zero()
        if adverse < Money.zero():
            raise ValueError("slippage cannot be negative")
        self._adverse = adverse

    def price(self, order: WorkingOrder, trade_price: Money) -> Money:
        slipped = Money(trade_price.amount + _DIRECTION[order.side] * self._adverse.amount)
        return slipped if _edge(order.side, order.limit_price, slipped) >= 0 else order.limit_price


class FillPolicy(Protocol):
    def execution_price(self, order: WorkingOrder, tick: Tick) -> Money | None:
        """The price the order trades at on this tick, or None if it does not trade."""
        ...


class LimitFillPolicy:
    def __init__(self, crossing: PriceCrossing, pricing: FillPricing) -> None:
        self._crossing = crossing
        self._pricing = pricing

    def execution_price(self, order: WorkingOrder, tick: Tick) -> Money | None:
        if not self._crossing.crosses(order, tick.ltp):
            return None
        return self._pricing.price(order, tick.ltp)


# ---- quantity: how many shares does the tick give us ---------------------------------------


class LiquidityModel(Protocol):
    def budget(self, traded_volume: int | None) -> int | None:
        """Shares the simulated account may take from one tick, shared by every order on the
        instrument in time priority. `traded_volume` is what traded since the previous tick
        (None when the feed does not say). None back means unlimited."""
        ...


class FullLiquidity:
    """Every eligible order fills completely on the tick that makes it eligible. Optimistic."""

    def budget(self, traded_volume: int | None) -> int | None:
        return None


class ParticipationLiquidity:
    """We can take at most `rate` of what traded since the last tick, across all our orders.

    Without a volume figure (LTP-only feed, first tick of the day) nothing can be assumed, so the
    budget is zero and the order waits: never fill on volume we cannot see."""

    def __init__(self, rate: Decimal) -> None:
        if not Decimal(0) < rate <= Decimal(1):
            raise ValueError("participation is a fraction in (0, 1]")
        self._rate = rate

    def budget(self, traded_volume: int | None) -> int | None:
        if traded_volume is None:
            return 0
        return floor(Decimal(traded_volume) * self._rate)


class CappedLiquidity:
    """At most `shares` per tick, whatever the tape offers: a simple, fixed partial-fill model."""

    def __init__(self, shares: int) -> None:
        if shares <= 0:
            raise ValueError("the per-tick cap must be positive")
        self._shares = shares

    def budget(self, traded_volume: int | None) -> int | None:
        return self._shares


# ---- stop-loss triggers --------------------------------------------------------------------


class TriggerRule(Protocol):
    def fires(self, order: WorkingOrder, tick: Tick) -> bool: ...


class StopTrigger:
    """A stop-loss sell triggers when the price falls to its trigger, a buy when it rises to it.
    The triggering tick never also fills the order: the limit order it becomes is first eligible
    on the NEXT tick, which is the conservative reading of "trigger, then place"."""

    def fires(self, order: WorkingOrder, tick: Tick) -> bool:
        if order.trigger_price is None:
            return False
        return _edge(order.side, order.trigger_price, tick.ltp) <= 0
