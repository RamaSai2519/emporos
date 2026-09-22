"""squeeze_breakout_v1 — trade a breakout only when it follows a genuine volatility compression.

`ATR / close` is ranked against its own trailing `squeeze_window` bars; a bar in the bottom
`squeeze_percentile` is "squeezed". Once squeezed for at least `min_squeeze_bars` in a row, the
first bar that CLOSES beyond the high/low of the prior `breakout_lookback` bars (by
`breakout_buffer_bps`) is the entry, in the breakout direction. Unlike donchian_v1, every day's
early range is not traded regardless of how tight it was; unlike orb_v1, the range is not fixed to
the session open. The stop and target are `stop_atr_mult`/`target_atr_mult` ATRs from entry, fixed
at entry (no trailing) so a losing trade exits fast rather than bleeding out — this is deliberately
a different exit mechanic from donchian_v1's trailing stop. At most `max_entries` per instrument
per day. Volatility history (the squeeze read) is NOT reset at the session boundary — a squeeze is
relative to the instrument's own recent volatility, not just today's; only the breakout channel and
entry count are day-scoped.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar

from pydantic import model_validator

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from emporos.strategies.config import (
    ExactDecimal,
    PositiveInt,
    ResolvedStrategyConfig,
    StrategyParameters,
)
from emporos.strategies.indicators import AverageTrueRange
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack, IntradayStrategy

_BPS = Decimal(10_000)


class SqueezeBreakoutParameters(StrategyParameters):
    atr_period: PositiveInt = 14
    squeeze_window: PositiveInt = 24
    squeeze_percentile: ExactDecimal = Decimal("0.20")
    min_squeeze_bars: PositiveInt = 3
    breakout_lookback: PositiveInt = 10
    breakout_buffer_bps: ExactDecimal = Decimal(5)
    stop_atr_mult: ExactDecimal = Decimal("1.0")
    target_atr_mult: ExactDecimal = Decimal("2.0")
    max_entries: PositiveInt = 1
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> SqueezeBreakoutParameters:
        if not 0 < self.squeeze_percentile < 1:
            raise ValueError("squeeze_percentile must be in (0, 1)")
        if self.stop_atr_mult <= 0 or self.target_atr_mult <= 0 or self.breakout_buffer_bps < 0:
            raise ValueError("stop and target must be positive, the buffer non-negative")
        return self


@dataclass
class _Track(DayTrack):
    atr: AverageTrueRange | None = None
    atr_pct_history: deque[Decimal] = field(default_factory=deque)
    squeezed_streak: int = 0
    highs: deque[Decimal] = field(default_factory=deque)
    lows: deque[Decimal] = field(default_factory=deque)
    entries: int = 0
    stop: Decimal | None = None
    target: Decimal | None = None


class SqueezeBreakoutV1(IntradayStrategy):
    name: ClassVar[str] = "squeeze_breakout_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = SqueezeBreakoutParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, SqueezeBreakoutParameters):
            raise TypeError("squeeze_breakout_v1 needs SqueezeBreakoutParameters")
        self._p: SqueezeBreakoutParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> _Track:
        return _Track(
            atr=AverageTrueRange(self._p.atr_period),
            atr_pct_history=deque(maxlen=self._p.squeeze_window),
        )

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _Track) and track.atr is not None
        if track.start_bar(bar):
            track.highs.clear()
            track.lows.clear()
            track.entries, track.stop, track.target = 0, None, None

        prior_streak = track.squeezed_streak
        was_squeeze_ready = len(track.atr_pct_history) == self._p.squeeze_window
        channel_high = max(track.highs) if len(track.highs) == self._p.breakout_lookback else None
        channel_low = min(track.lows) if len(track.lows) == self._p.breakout_lookback else None

        close = bar.close.amount
        atr = track.atr.update(bar.high.amount, bar.low.amount, close)
        track.highs.append(bar.high.amount)
        track.lows.append(bar.low.amount)
        if len(track.highs) > self._p.breakout_lookback:
            track.highs.popleft()
            track.lows.popleft()
        if atr is not None and close != ar.ZERO:
            track.squeezed_streak = self._advance_squeeze(track, ar.div(atr, close), prior_streak)

        if not live or not was_squeeze_ready or channel_high is None or channel_low is None:
            return
        assert atr is not None  # atr readiness is implied by was_squeeze_ready
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held)
        elif (
            track.entries < self._p.max_entries
            and self._entries_open(bar)
            and prior_streak >= self._p.min_squeeze_bars
        ):
            self._maybe_enter(track, bar, atr, channel_high, channel_low)

    def _advance_squeeze(self, track: _Track, atr_pct: Decimal, prior_streak: int) -> int:
        history = track.atr_pct_history
        history.append(atr_pct)
        if len(history) < self._p.squeeze_window:
            return 0
        at_or_below = sum(1 for sample in history if sample <= atr_pct)
        percentile = ar.div(Decimal(at_or_below), Decimal(len(history)))
        return prior_streak + 1 if percentile <= self._p.squeeze_percentile else 0

    def _manage(self, track: _Track, bar: Candle, held: Position) -> None:
        close = bar.close.amount
        if track.stop is None or track.target is None:
            return
        if held.is_long and (close <= track.stop or close >= track.target):
            self._exit(bar, held, f"long {'stopped' if close <= track.stop else 'target hit'}")
            track.stop = track.target = None
        elif not held.is_long and (close >= track.stop or close <= track.target):
            self._exit(bar, held, f"short {'stopped' if close >= track.stop else 'target hit'}")
            track.stop = track.target = None

    def _maybe_enter(
        self, track: _Track, bar: Candle, atr: Decimal, high: Decimal, low: Decimal
    ) -> None:
        close = bar.close.amount
        buffer = self._p.breakout_buffer_bps / _BPS
        stop_distance = ar.mul(atr, self._p.stop_atr_mult)
        target_distance = ar.mul(atr, self._p.target_atr_mult)
        if close > high * (1 + buffer):
            side, stop, target = OrderSide.BUY, close - stop_distance, close + target_distance
        elif self._p.allow_short and close < low * (1 - buffer):
            side, stop, target = OrderSide.SELL, close + stop_distance, close - target_distance
        else:
            return
        if self._enter(
            bar,
            side,
            f"squeeze ({self._p.min_squeeze_bars}+ bars) broke the "
            f"{self._p.breakout_lookback}-bar range {low}..{high}",
        ):
            track.entries += 1
            track.stop, track.target = stop, target
