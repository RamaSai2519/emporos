"""momentum_v1 — the reference strategy (plan.md §9, EM-69).

Fast EMA crossing above the slow EMA, confirmed by RSI, on closed bars of the configured timeframe.
Long-only (cash intraday):

* ENTRY  — the fast EMA crosses ABOVE the slow EMA, RSI is inside [rsi_entry_min, rsi_entry_max]
           (momentum, but not already stretched), the strategy is flat in that instrument, and the
           bar closed before `session.no_new_entries_after`. Sized to `risk.max_position_value`.
* EXIT   — the fast EMA crosses BELOW the slow EMA while the strategy holds the instrument.
           Exits are never blocked by the session cut-off.

Signals are edge-triggered (one per crossover), so the strategy needs no memory of its own orders:
a rejected or unfilled signal is simply not repeated until the next crossover. Warm-up bars (until
the slow EMA and the RSI are ready, plus one bar to see a crossover) produce no signals. A bar
flagged `partial` still feeds the indicators, so the series has no hole, but never triggers a
signal: nothing is traded on data known to be incomplete. A bar at or before the last one seen for
its instrument is ignored, so a redelivered bar cannot advance the indicators twice.

The limit price is the bar's close; execution (Phase 12) makes it marketable per
`execution.limit_buffer_bps`. Stops and targets (`risk.stop_loss_pct`, `risk.target_pct`) belong
to risk and execution, which own the orders that protect a position.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import ClassVar, Self

from pydantic import model_validator

from emporos.core.clock import IST
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from emporos.domain.ticks import Tick
from emporos.strategies.base import Strategy
from emporos.strategies.config import (
    ExactDecimal,
    PositiveInt,
    ResolvedStrategyConfig,
    StrategyParameters,
)
from emporos.strategies.context import StrategyContext
from emporos.strategies.indicators import ExponentialMovingAverage, RelativeStrengthIndex
from emporos.strategies.indicators import arithmetic as ar

WARMUP_FACTOR = 5  # bars of history replayed at start, as a multiple of the longest indicator
TWO_PLACES = Decimal("0.01")


class MomentumParameters(StrategyParameters):
    fast_ema: PositiveInt
    slow_ema: PositiveInt
    rsi_period: PositiveInt
    rsi_entry_min: ExactDecimal = Decimal(50)
    rsi_entry_max: ExactDecimal = Decimal(70)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.fast_ema >= self.slow_ema:
            raise ValueError("fast_ema must be shorter than slow_ema")
        if not 0 <= self.rsi_entry_min < self.rsi_entry_max <= 100:
            raise ValueError("RSI bounds need 0 <= rsi_entry_min < rsi_entry_max <= 100")
        return self


@dataclass
class _Track:
    """Indicator state for one instrument."""

    fast: ExponentialMovingAverage
    slow: ExponentialMovingAverage
    rsi: RelativeStrengthIndex
    spread: Decimal | None = None  # fast - slow after the previous bar
    last_ts: datetime | None = None


class MomentumV1(Strategy):
    name: ClassVar[str] = "momentum_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = MomentumParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        super().__init__(config)
        if not isinstance(config.parameters, MomentumParameters):
            raise TypeError("momentum_v1 needs MomentumParameters")
        self._params: MomentumParameters = config.parameters
        self._tracks: dict[str, _Track] = {
            instrument_id: self._new_track() for instrument_id in config.instrument_ids
        }
        self._outbox: deque[Signal] = deque()
        self._ctx: StrategyContext | None = None

    def _new_track(self) -> _Track:
        return _Track(
            ExponentialMovingAverage(self._params.fast_ema),
            ExponentialMovingAverage(self._params.slow_ema),
            RelativeStrengthIndex(self._params.rsi_period),
        )

    def initialize(self, ctx: StrategyContext) -> None:
        """Warm the indicators from closed history so a mid-day start has no blind prefix."""
        self._ctx = ctx
        depth = WARMUP_FACTOR * max(self._params.slow_ema, self._params.rsi_period + 1)
        for instrument_id, track in self._tracks.items():
            for bar in ctx.history.bars(instrument_id, self._config.timeframe, depth):
                self._advance(track, bar)

    def on_market_data(self, event: Candle | Tick) -> None:
        if not isinstance(event, Candle) or event.timeframe is not self._config.timeframe:
            return
        track = self._tracks.get(event.instrument_id)
        if track is None or (track.last_ts is not None and event.ts <= track.last_ts):
            return
        previous_spread = track.spread
        self._advance(track, event)
        if event.partial or previous_spread is None or track.spread is None:
            return
        if previous_spread <= 0 < track.spread:
            self._maybe_enter(event, track)
        elif previous_spread >= 0 > track.spread:
            self._maybe_exit(event, track)

    def generate_signal(self) -> Signal | None:
        return self._outbox.popleft() if self._outbox else None

    def _advance(self, track: _Track, bar: Candle) -> None:
        price = bar.close.amount
        fast, slow = track.fast.update(price), track.slow.update(price)
        track.rsi.update(price)
        track.spread = None if fast is None or slow is None else ar.sub(fast, slow)
        track.last_ts = bar.ts

    def _maybe_enter(self, bar: Candle, track: _Track) -> None:
        ctx = self._require_ctx()
        rsi = track.rsi.value
        if rsi is None or not self._params.rsi_entry_min <= rsi <= self._params.rsi_entry_max:
            return
        if not ctx.positions.position(bar.instrument_id).is_flat:
            return
        if bar.closes_at.astimezone(IST).time() >= self._config.session.no_new_entries_after:
            return
        affordable = ar.div(self._config.risk.max_position_value, bar.close.amount)
        quantity = int(affordable.to_integral_value(ROUND_FLOOR))
        if quantity < 1:
            ctx.logger.info("%s: max_position_value buys less than one share", bar.instrument_id)
            return
        assert track.fast.value is not None and track.slow.value is not None
        reason = (
            f"EMA{self._params.fast_ema} {self._show(track.fast.value)} crossed above "
            f"EMA{self._params.slow_ema} {self._show(track.slow.value)}; "
            f"RSI{self._params.rsi_period} {self._show(rsi)}"
        )
        self._emit(bar, SignalKind.ENTRY, OrderSide.BUY, quantity, reason)

    def _maybe_exit(self, bar: Candle, track: _Track) -> None:
        held = self._require_ctx().positions.position(bar.instrument_id)
        if not held.is_long:
            return
        assert track.fast.value is not None and track.slow.value is not None
        reason = (
            f"EMA{self._params.fast_ema} {self._show(track.fast.value)} crossed below "
            f"EMA{self._params.slow_ema} {self._show(track.slow.value)}"
        )
        self._emit(bar, SignalKind.EXIT, OrderSide.SELL, held.net_quantity, reason)

    def _emit(
        self, bar: Candle, kind: SignalKind, side: OrderSide, quantity: int, why: str
    ) -> None:
        ctx = self._require_ctx()
        self._outbox.append(
            Signal(
                strategy_run_id=ctx.run_id,
                instrument_id=bar.instrument_id,
                kind=kind,
                side=side,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                limit_price=Money(bar.close.amount),
                ts=bar.closes_at,
                reason=why,
            )
        )

    @staticmethod
    def _show(value: Decimal) -> str:
        return format(value.quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN, context=ar.CONTEXT), "f")

    def _require_ctx(self) -> StrategyContext:
        if self._ctx is None:
            raise RuntimeError("momentum_v1 used before initialize()")
        return self._ctx
