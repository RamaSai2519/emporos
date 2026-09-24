"""Shared machinery for intraday strategies on closed bars: per-instrument day state, sizing, the
entry cut-off, and signal emission. A concrete strategy supplies only its rules.

* The IST trading day is derived from each bar's own timestamp (a strategy never reads a clock), so
  the same bars give the same signals in a backtest, in paper and live.
* Warm-up bars replay through the same path with `live=False`: indicators and day state advance, but
  no signal can result, so a mid-day start has no blind prefix and no phantom trade.
* A `partial` bar (one spanning a feed gap) advances the state but never triggers a trade, and a bar
  at or before the last one seen for its instrument is ignored, so a redelivered bar cannot advance
  anything twice.
* Long and short are both possible (cash intraday allows either); `allow_short` turns shorting off.
* Exits are never blocked by the entry cut-off; the session's own square-off flattens what is left.
"""

from __future__ import annotations

from abc import abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.positions import Position
from emporos.domain.signals import Signal, SignalKind
from emporos.domain.ticks import Tick
from emporos.strategies.base import Strategy
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.context import StrategyContext
from emporos.strategies.indicators import arithmetic as ar

_HUNDRED = Decimal(100)
WARMUP_BARS = 400  # enough for the slowest indicator any built-in uses, across several days


class SessionVwap:
    """Volume-weighted average price since the session opened, from typical prices (H+L+C)/3.
    Resets when the IST date changes. Zero volume so far means no VWAP (`None`)."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._price_volume = ar.ZERO
        self._volume = 0

    def update(self, bar: Candle) -> Decimal | None:
        day = bar.ts.astimezone(IST).date()
        if day != self._day:
            self._day, self._price_volume, self._volume = day, ar.ZERO, 0
        typical = ar.div(bar.high.amount + bar.low.amount + bar.close.amount, Decimal(3))
        self._price_volume = ar.add(self._price_volume, ar.mul(typical, Decimal(bar.volume)))
        self._volume += bar.volume
        return self.value

    @property
    def value(self) -> Decimal | None:
        if self._volume == 0:
            return None
        return ar.div(self._price_volume, Decimal(self._volume))


@dataclass
class DayTrack:
    """State every intraday strategy keeps per instrument."""

    last_ts: datetime | None = None
    day: date | None = None
    bar_of_day: int = 0  # bars seen so far today, this one included
    vwap: SessionVwap = field(default_factory=SessionVwap)

    def start_bar(self, bar: Candle) -> bool:
        """Advance the day counters; True when this bar opened a new IST day."""
        day = bar.ts.astimezone(IST).date()
        new_day = day != self.day
        if new_day:
            self.day, self.bar_of_day = day, 0
        self.bar_of_day += 1
        self.last_ts = bar.ts
        return new_day


class IntradayStrategy(Strategy):
    """Template: `_new_track`, `_observe`. Everything else is provided."""

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        super().__init__(config)
        self._tracks: dict[str, DayTrack] = {i: self._new_track() for i in config.instrument_ids}
        self._outbox: deque[Signal] = deque()
        self._ctx: StrategyContext | None = None

    @abstractmethod
    def _new_track(self) -> DayTrack: ...

    @abstractmethod
    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        """Advance indicators and day state with this closed bar; when `live`, apply the rules."""

    def initialize(self, ctx: StrategyContext) -> None:
        self._ctx = ctx
        for instrument_id, track in self._tracks.items():
            for bar in ctx.history.bars(instrument_id, self._config.timeframe, WARMUP_BARS):
                if track.last_ts is None or bar.ts > track.last_ts:
                    self._observe(track, bar, live=False)

    def on_market_data(self, event: Candle | Tick) -> None:
        if not isinstance(event, Candle) or event.timeframe is not self._config.timeframe:
            return
        track = self._tracks.get(event.instrument_id)
        if track is None or (track.last_ts is not None and event.ts <= track.last_ts):
            return
        self._observe(track, event, live=not event.partial)

    def generate_signal(self) -> Signal | None:
        return self._outbox.popleft() if self._outbox else None

    # --- helpers for subclasses -------------------------------------------------------------
    def _held(self, bar: Candle) -> Position:
        return self._require_ctx().positions.position(bar.instrument_id)

    def _entries_open(self, bar: Candle) -> bool:
        return bar.closes_at.astimezone(IST).time() < self._config.session.no_new_entries_after

    def _quantity(self, bar: Candle) -> int:
        affordable = ar.div(self._config.risk.max_position_value, bar.close.amount)
        return int(affordable.to_integral_value(ROUND_FLOOR))

    def _enter(self, bar: Candle, side: OrderSide, why: str, stop: Decimal | None = None) -> bool:
        """Queue an entry sized to `risk.max_position_value`; False if it buys under one share.

        `stop` is the strategy's own protective stop. One on the wrong side of the entry (or none)
        is replaced by the configured `risk.stop_loss_pct`, so an entry always states its risk."""
        quantity = self._quantity(bar)
        if quantity < 1:
            return False
        protective = self._protective_stop(bar, side, stop)
        self._emit(bar, SignalKind.ENTRY, side, quantity, why, protective)
        return True

    def _protective_stop(self, bar: Candle, side: OrderSide, stop: Decimal | None) -> Decimal:
        close = bar.close.amount
        buying = side is OrderSide.BUY
        if stop is not None and stop > 0 and (stop < close if buying else stop > close):
            return stop
        distance = ar.div(ar.mul(close, self._config.risk.stop_loss_pct), _HUNDRED)
        return ar.sub(close, distance) if buying else ar.add(close, distance)

    def _exit(self, bar: Candle, held: Position, why: str) -> None:
        side = OrderSide.SELL if held.is_long else OrderSide.BUY
        self._emit(bar, SignalKind.EXIT, side, abs(held.net_quantity), why)

    def _emit(
        self,
        bar: Candle,
        kind: SignalKind,
        side: OrderSide,
        quantity: int,
        why: str,
        protective_stop: Decimal | None = None,
    ) -> None:
        self._outbox.append(
            Signal(
                strategy_run_id=self._require_ctx().run_id,
                instrument_id=bar.instrument_id,
                kind=kind,
                side=side,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                limit_price=Money(bar.close.amount),
                ts=bar.closes_at,
                reason=why,
                protective_stop=None if protective_stop is None else Money(protective_stop),
            )
        )

    def _require_ctx(self) -> StrategyContext:
        if self._ctx is None:
            raise RuntimeError(f"{self.name} used before initialize()")
        return self._ctx
