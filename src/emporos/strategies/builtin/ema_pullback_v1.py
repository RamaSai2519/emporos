"""ema_pullback_v1 — buy the dip to the 9 EMA inside an up-trend, sell the rally in a down-trend.

UP is: the fast EMA above the slow EMA and the close above the slow EMA (and above the session
VWAP when `use_vwap`). The entry is a PULLBACK that has held: the previous bar's low reached the
fast EMA without its close breaking the slow EMA, and this bar closes back above the fast EMA as a
bullish candle. Down-trends mirror it. The trade exits when the close goes through the slow EMA
against it, at `stop_atr_mult` ATRs against the entry, or `target_r` times that in favour. No
entries in the first `skip_bars` bars, after `session.no_new_entries_after`, or beyond
`max_entries` per instrument per day.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from pydantic import model_validator

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from emporos.strategies.config import (
    ExactDecimal,
    NonNegativeInt,
    PositiveInt,
    ResolvedStrategyConfig,
    StrategyParameters,
)
from emporos.strategies.indicators import AverageTrueRange, ExponentialMovingAverage
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack, IntradayStrategy


class EmaPullbackParameters(StrategyParameters):
    fast_ema: PositiveInt = 9
    slow_ema: PositiveInt = 20
    atr_period: PositiveInt = 14
    use_vwap: bool = False
    stop_atr_mult: ExactDecimal = Decimal("1.5")
    target_r: ExactDecimal = Decimal(2)
    skip_bars: NonNegativeInt = 6
    max_entries: PositiveInt = 2
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> EmaPullbackParameters:
        if self.fast_ema >= self.slow_ema:
            raise ValueError("fast_ema must be shorter than slow_ema")
        if self.stop_atr_mult <= 0 or self.target_r <= 0:
            raise ValueError("stop_atr_mult and target_r must be positive")
        return self


@dataclass
class _Track(DayTrack):
    fast: ExponentialMovingAverage | None = None
    slow: ExponentialMovingAverage | None = None
    atr: AverageTrueRange | None = None
    prev: Candle | None = None
    prev_slow: Decimal | None = None
    entries: int = 0
    stop: Decimal | None = None
    target: Decimal | None = None


class EmaPullbackV1(IntradayStrategy):
    name: ClassVar[str] = "ema_pullback_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = EmaPullbackParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, EmaPullbackParameters):
            raise TypeError("ema_pullback_v1 needs EmaPullbackParameters")
        self._p: EmaPullbackParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> _Track:
        return _Track(
            fast=ExponentialMovingAverage(self._p.fast_ema),
            slow=ExponentialMovingAverage(self._p.slow_ema),
            atr=AverageTrueRange(self._p.atr_period),
        )

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _Track) and track.fast and track.slow and track.atr
        if track.start_bar(bar):
            track.entries, track.stop, track.target = 0, None, None
        previous, previous_slow = track.prev, track.prev_slow
        track.prev = bar
        vwap = track.vwap.update(bar)
        fast = track.fast.update(bar.close.amount)
        slow = track.slow.update(bar.close.amount)
        track.prev_slow = slow
        atr = track.atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        if not live or None in (vwap, fast, slow, atr, previous_slow) or previous is None:
            return
        assert vwap is not None and fast is not None and slow is not None and atr is not None
        assert previous_slow is not None
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held, slow)
        elif (
            track.bar_of_day > self._p.skip_bars
            and track.entries < self._p.max_entries
            and self._entries_open(bar)
        ):
            self._maybe_enter(track, bar, previous, previous_slow, vwap, fast, slow, atr)

    def _manage(self, track: _Track, bar: Candle, held: Position, slow: Decimal) -> None:
        close = bar.close.amount
        if track.stop is None or track.target is None:
            return
        if held.is_long:
            stopped, at_target, broke = close <= track.stop, close >= track.target, close < slow
        else:
            stopped, at_target, broke = close >= track.stop, close <= track.target, close > slow
        reason = "stopped" if stopped else "at target" if at_target else "broke the slow EMA"
        if stopped or at_target or broke:
            self._exit(bar, held, reason)
            track.stop = track.target = None

    def _maybe_enter(
        self, track: _Track, bar: Candle, previous: Candle, previous_slow: Decimal, vwap: Decimal,
        fast: Decimal, slow: Decimal, atr: Decimal,
    ) -> None:  # fmt: skip
        close = bar.close.amount
        up = fast > slow and close > slow and (not self._p.use_vwap or close > vwap)
        down = (
            self._p.allow_short
            and fast < slow
            and close < slow
            and (not self._p.use_vwap or close < vwap)
        )
        dipped = previous.low.amount <= fast and previous.close.amount >= previous_slow
        rallied = previous.high.amount >= fast and previous.close.amount <= previous_slow
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if up and dipped and close > fast and bar.close > bar.open:
            side, stop, target = (
                OrderSide.BUY,
                close - distance,
                close + distance * self._p.target_r,
            )
        elif down and rallied and close < fast and bar.close < bar.open:
            side, stop, target = (
                OrderSide.SELL,
                close + distance,
                close - distance * self._p.target_r,
            )
        else:
            return
        if self._enter(
            bar, side, f"pullback to the {self._p.fast_ema} EMA {fast:.2f} held", stop=stop
        ):
            track.entries += 1
            track.stop, track.target = stop, target
