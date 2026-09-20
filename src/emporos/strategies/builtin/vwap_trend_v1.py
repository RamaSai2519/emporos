"""vwap_trend_v1 — trade with the trend when price, VWAP and two EMAs all agree.

UP is: the bar closes above the session VWAP and the fast EMA is above the slow one; DOWN is the
mirror. In `pullback = false` mode the entry is CONTINUATION: an up (down) bar that closes above
(below) the previous bar's high (low). In `pullback = true` mode it is a PULLBACK: an up bar that
closes bullish after the previous bar's low came within `pullback_atr` ATRs of the fast EMA (down
mirrored). The trend is lost, and the trade exits, when the close crosses VWAP against it or the
EMAs cross; it also exits at `stop_atr_mult` ATRs against the entry or `target_r` times that
distance in favour. No entries in the first `skip_bars` bars (VWAP means little yet), after
`session.no_new_entries_after`, or beyond `max_entries` per instrument per day.
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


class VwapTrendParameters(StrategyParameters):
    fast_ema: PositiveInt = 9
    slow_ema: PositiveInt = 21
    atr_period: PositiveInt = 14
    pullback: bool = False
    pullback_atr: ExactDecimal = Decimal("0.5")
    stop_atr_mult: ExactDecimal = Decimal("1.5")
    target_r: ExactDecimal = Decimal(2)
    skip_bars: NonNegativeInt = 6
    max_entries: PositiveInt = 2
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> VwapTrendParameters:
        if self.fast_ema >= self.slow_ema:
            raise ValueError("fast_ema must be shorter than slow_ema")
        if self.pullback_atr <= 0 or self.stop_atr_mult <= 0 or self.target_r <= 0:
            raise ValueError("pullback_atr, stop_atr_mult and target_r must be positive")
        return self


@dataclass
class _Track(DayTrack):
    fast: ExponentialMovingAverage | None = None
    slow: ExponentialMovingAverage | None = None
    atr: AverageTrueRange | None = None
    prev: Candle | None = None
    entries: int = 0
    stop: Decimal | None = None
    target: Decimal | None = None


class VwapTrendV1(IntradayStrategy):
    name: ClassVar[str] = "vwap_trend_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = VwapTrendParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, VwapTrendParameters):
            raise TypeError("vwap_trend_v1 needs VwapTrendParameters")
        self._p: VwapTrendParameters = config.parameters
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
            track.entries, track.stop, track.target, track.prev = 0, None, None, None
        previous, track.prev = track.prev, bar
        vwap = track.vwap.update(bar)
        fast = track.fast.update(bar.close.amount)
        slow = track.slow.update(bar.close.amount)
        atr = track.atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        if not live or None in (vwap, fast, slow, atr) or previous is None:
            return
        assert vwap is not None and fast is not None and slow is not None and atr is not None
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held, vwap, fast, slow)
        elif (
            track.bar_of_day > self._p.skip_bars
            and track.entries < self._p.max_entries
            and self._entries_open(bar)
        ):
            self._maybe_enter(track, bar, previous, vwap, fast, slow, atr)

    def _manage(
        self,
        track: _Track,
        bar: Candle,
        held: Position,
        vwap: Decimal,
        fast: Decimal,
        slow: Decimal,
    ) -> None:
        close = bar.close.amount
        if track.stop is None or track.target is None:
            return
        if held.is_long:
            reason = self._reason(
                close <= track.stop, close >= track.target, close < vwap or fast < slow
            )
        else:
            reason = self._reason(
                close >= track.stop, close <= track.target, close > vwap or fast > slow
            )
        if reason is not None:
            self._exit(bar, held, reason)
            track.stop = track.target = None

    @staticmethod
    def _reason(stopped: bool, at_target: bool, trend_lost: bool) -> str | None:
        if stopped:
            return "stopped"
        if at_target:
            return "at target"
        return "trend lost" if trend_lost else None

    def _maybe_enter(
        self, track: _Track, bar: Candle, previous: Candle, vwap: Decimal, fast: Decimal,
        slow: Decimal, atr: Decimal,
    ) -> None:  # fmt: skip
        close = bar.close.amount
        up = close > vwap and fast > slow
        down = self._p.allow_short and close < vwap and fast < slow
        near = ar.mul(atr, self._p.pullback_atr)
        if self._p.pullback:
            long_signal = up and previous.low.amount <= fast + near and bar.close > bar.open
            short_signal = down and previous.high.amount >= fast - near and bar.close < bar.open
        else:
            long_signal = up and close > previous.high.amount
            short_signal = down and close < previous.low.amount
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if long_signal:
            side, stop, target = (
                OrderSide.BUY,
                close - distance,
                close + distance * self._p.target_r,
            )
        elif short_signal:
            side, stop, target = (
                OrderSide.SELL,
                close + distance,
                close - distance * self._p.target_r,
            )
        else:
            return
        mode = "pullback" if self._p.pullback else "continuation"
        if self._enter(bar, side, f"{mode} with VWAP {vwap:.2f} and EMAs {fast:.2f}/{slow:.2f}"):
            track.entries += 1
            track.stop, track.target = stop, target
