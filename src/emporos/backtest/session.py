"""`BacktestSession` — everything that happens around the strategy's bars (plan.md §10 flow).

For each closed bar, in this order (the clock already stands at the bar's close):

  1. the equity point of the PREVIOUS moment is recorded, before this bar changes anything;
  2. answers to earlier signals (acks, rejections) reach the book and the strategy;
  3. the exchange lets this bar act on resting orders: fills are charged, booked, and told to the
     strategy BEFORE it sees the bar (a fill applies before the next signal evaluates);
  4. the instrument is marked to the bar's close;
  5. from `square_off_at`, resting orders are cancelled and the position gets its exit signal.

Only then does the runner hand the bar to the strategy. At the end of each day the exchange cancels
the day's unfilled orders and the broker's own square-off closes any position still open; the
strategy is told of both BEFORE its `on_session_end`, and what it sends there cannot rest overnight.
"""

from __future__ import annotations

from datetime import date, datetime

from emporos.backtest.broker import SimulatedBroker
from emporos.backtest.flow import EventSettler, OrderEventQueue, OrderFlow, RunCounters
from emporos.backtest.portfolio import BacktestPortfolio
from emporos.backtest.progress import (
    BacktestProgress,
    BacktestProgressSink,
    NullBacktestProgressSink,
)
from emporos.backtest.square_off import ForcedClosePricing, SessionSquareOff
from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide


class BacktestSession:
    def __init__(
        self,
        broker: SimulatedBroker,
        portfolio: BacktestPortfolio,
        flow: OrderFlow,
        queue: OrderEventQueue,
        settler: EventSettler,
        square_off: SessionSquareOff,
        forced: ForcedClosePricing,
        counters: RunCounters,
        progress: BacktestProgressSink | None = None,
    ) -> None:
        self._broker = broker
        self._portfolio = portfolio
        self._flow = flow
        self._queue = queue
        self._settler = settler
        self._square_off = square_off
        self._forced = forced
        self._counters = counters
        self._progress: BacktestProgressSink = progress or NullBacktestProgressSink()
        self._bars = 0  # bars replayed this run, warm-up bars excluded
        self._pending: datetime | None = None  # the moment whose equity point is not yet recorded

    async def before_bar(self, bar: Candle) -> None:
        if self._pending is not None and bar.closes_at > self._pending:
            self._record_point()
        await self._settler.settle()
        for event in self._broker.match(bar):
            self._queue.push(event)
        await self._settler.settle()
        self._portfolio.mark(bar.instrument_id, bar.close)
        self._pending = bar.closes_at
        await self._square_off_if_due(bar)
        self._report(bar)

    async def session_ending(self, day: date) -> None:
        """The exchange's end of day, while the strategy can still hear about it: unfilled orders
        expire, then the broker's own square-off closes whatever is still open."""
        await self._settler.settle()
        for event in self._broker.expire_open_orders():
            self._queue.push(event)
        await self._settler.settle()
        for position in self._portfolio.open_positions():
            side = OrderSide.SELL if position.is_long else OrderSide.BUY
            last = self._portfolio.last_price(position.instrument_id)
            price = self._forced.price(side, last)
            quantity = abs(position.net_quantity)
            self._queue.push(self._broker.square_off(position.instrument_id, side, quantity, price))
        await self._settler.settle()

    async def session_closed(self, day: date) -> None:
        """After the strategy's `on_session_end`: whatever IT sent cannot rest into tomorrow."""
        await self._settler.settle()
        for event in self._broker.expire_open_orders():
            self._queue.push(event)
        await self._settler.settle()
        if self._pending is not None:
            self._record_point()

    async def _square_off_if_due(self, bar: Candle) -> None:
        plan = self._square_off.plan(
            bar, self._portfolio.position(bar.instrument_id), self._broker.open_orders()
        )
        if plan is None:
            return
        for order_id in plan.cancel:
            self._queue.push(self._broker.cancel(order_id))
        await self._flow.submit_system(plan.exit)
        await self._settler.settle()

    def _report(self, bar: Candle) -> None:
        """One progress snapshot per closed bar. A sink that raises is dropped, never the run:
        the display must not be able to change (or stop) a backtest."""
        self._bars += 1
        try:
            self._progress.report(
                BacktestProgress(
                    day=bar.closes_at.astimezone(IST).date(),
                    closes_at=bar.closes_at,
                    bars_seen=self._bars,
                    equity=self._portfolio.equity(),
                    gross_exposure=self._portfolio.gross_exposure(),
                    open_positions=len(self._portfolio.open_positions()),
                    closed_trades=len(self._portfolio.closed_trades),
                    signals=self._counters.signals,
                    orders=self._counters.orders,
                    fills=self._counters.fills,
                    cancelled_or_expired=self._counters.cancelled_or_expired,
                    forced_square_offs=self._counters.forced_square_offs,
                )
            )
        except Exception:
            self._progress = NullBacktestProgressSink()

    def _record_point(self) -> None:
        assert self._pending is not None
        self._portfolio.record_point(self._pending)
        self._pending = None
