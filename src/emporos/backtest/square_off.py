"""End of the trading day for an intraday product (plan.md §9 `session.square_off_at`).

Cash intraday positions may not be carried overnight. Risk and execution (Phases 11-12) will own
this in production; the backtest needs a minimal stand-in so a run never holds a position past its
session, and flags it as such:

* at `square_off_at` each instrument with a position gets its resting orders cancelled and ONE exit
  signal for the whole position, sent through the same gate, pricing and broker as any signal;
* from then on the SESSION owns that instrument for the day: further signals from the strategy are
  refused (`blocks`), or its own exit and the strategy's would both fill and sell twice;
* whatever is STILL open when the session closes is closed by the broker's own end-of-day
  square-off at the last price, `forced_close_penalty_bps` against us.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from emporos.backtest.broker import OrderSnapshot
from emporos.core.clock import IST, Clock
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.positions import Position
from emporos.domain.signals import Signal, SignalKind

_BPS = Decimal(10_000)
_PAISA = Decimal("0.01")


@dataclass(frozen=True)
class SquareOffPlan:
    cancel: tuple[str, ...]  # order ids to cancel first
    exit: Signal


class SessionSquareOff:
    def __init__(
        self, square_off_at: time, instrument_owner: Callable[[str], str], clock: Clock
    ) -> None:
        """`instrument_owner` resolves an instrument to the strategy_run_id whose position it is
        (EM-158), the same seam `BacktestSession` uses for the broker's own forced square-off: a
        single-strategy run passes a constant function, a multi-strategy run a real lookup."""
        self._at = square_off_at
        self._owner = instrument_owner
        self._clock = clock
        self._planned: set[tuple[date, str]] = set()

    def blocks(self, signal: Signal) -> bool:
        """The strategy's signal for an instrument the session has already begun to close out
        today. The day is the simulation clock's, never the signal's own (strategy-written) ts."""
        today = self._clock.now().astimezone(IST).date()
        return (today, signal.instrument_id) in self._planned

    def plan(
        self, bar: Candle, position: Position, resting: tuple[OrderSnapshot, ...]
    ) -> SquareOffPlan | None:
        """A plan the first time, on each day, that this instrument's bar closes at or after the
        square-off time while the position is open."""
        closes = bar.closes_at.astimezone(IST)
        key = (closes.date(), bar.instrument_id)
        if position.is_flat or closes.time() < self._at or key in self._planned:
            return None
        self._planned.add(key)
        side = OrderSide.SELL if position.is_long else OrderSide.BUY
        exit_signal = Signal(
            strategy_run_id=self._owner(bar.instrument_id),
            instrument_id=bar.instrument_id,
            kind=SignalKind.EXIT,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=abs(position.net_quantity),
            limit_price=bar.close,
            ts=bar.closes_at,
            reason=f"session square-off at {self._at.strftime('%H:%M')} IST",
        )
        mine = tuple(o.order_id for o in resting if o.request.instrument_id == bar.instrument_id)
        return SquareOffPlan(mine, exit_signal)


class ForcedClosePricing:
    """The price of the broker's own end-of-day square-off: the last price, moved against us."""

    def __init__(self, penalty_bps: Decimal) -> None:
        if penalty_bps < 0:
            raise ValueError("the penalty cannot be negative")
        self._penalty = penalty_bps / _BPS

    def price(self, closing_side: OrderSide, last: Money) -> Money:
        """`closing_side` is the side of the closing order: a BUY covers a short (pays more)."""
        if closing_side is OrderSide.BUY:
            return Money((last.amount * (1 + self._penalty)).quantize(_PAISA, rounding=ROUND_UP))
        return Money((last.amount * (1 - self._penalty)).quantize(_PAISA, rounding=ROUND_DOWN))
