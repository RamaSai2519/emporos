"""liquidity_thrust_v1 (EM-171/EM-173) — trade a rare volume-surge bar as a proxy for informed
order flow.

Every other built-in strategy decides from PRICE alone (a channel, an oscillator level, a moving
average). This one decides from participation: a bar whose volume is `vol_surge_mult` times its
own trailing `vol_window`-bar average volume, with a wide-enough range (`min_range_bps` of its
close) and a close near that bar's own extreme (`close_location_min` of the high-low range from the
low, or from the high on the short side), is read as a thrust that outside money is showing up
behind. The volume baseline is NOT reset at the session boundary (an instrument's typical liquidity
is not a fact about today alone); only the entry count and the open trade's stop/target are
day-scoped, matching squeeze_breakout_v1's convention for its own non-day-scoped read.

The exit is a fixed stop and target, `stop_atr_mult`/`target_atr_mult` ATRs from entry (no
trailing), with `target_atr_mult` deliberately large (default 4 ATRs): EM-114/EM-118 found every
prior candidate lost to a ~0.37% gross-per-trade cost hurdle at the benchmark's position size, and
traded often enough that no single win carried its own weight. This strategy is built to be
selective on purpose — most bars are ignored — so what does trade is meant to be a bigger, rarer
move, not a scalp. At most `max_entries` per instrument per day.
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


class LiquidityThrustParameters(StrategyParameters):
    atr_period: PositiveInt = 14
    vol_window: PositiveInt = 20
    vol_surge_mult: ExactDecimal = Decimal("3.0")
    close_location_min: ExactDecimal = Decimal("0.75")
    min_range_bps: ExactDecimal = Decimal(40)
    stop_atr_mult: ExactDecimal = Decimal("1.0")
    target_atr_mult: ExactDecimal = Decimal("4.0")
    max_entries: PositiveInt = 1
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> LiquidityThrustParameters:
        if self.vol_surge_mult <= 1:
            raise ValueError("vol_surge_mult must exceed 1: this is a SURGE over the baseline")
        if not 0 < self.close_location_min < 1:
            raise ValueError("close_location_min must be in (0, 1)")
        if self.min_range_bps < 0:
            raise ValueError("min_range_bps cannot be negative")
        if self.stop_atr_mult <= 0 or self.target_atr_mult <= 0:
            raise ValueError("stop and target must be positive")
        return self


@dataclass
class _Track(DayTrack):
    atr: AverageTrueRange | None = None
    volumes: deque[int] = field(default_factory=deque)
    entries: int = 0
    stop: Decimal | None = None
    target: Decimal | None = None


class LiquidityThrustV1(IntradayStrategy):
    name: ClassVar[str] = "liquidity_thrust_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = LiquidityThrustParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, LiquidityThrustParameters):
            raise TypeError("liquidity_thrust_v1 needs LiquidityThrustParameters")
        self._p: LiquidityThrustParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> _Track:
        return _Track(
            atr=AverageTrueRange(self._p.atr_period),
            volumes=deque(maxlen=self._p.vol_window),
        )

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _Track) and track.atr is not None
        if track.start_bar(bar):
            track.entries, track.stop, track.target = 0, None, None

        prior_avg_volume = (
            ar.div(Decimal(sum(track.volumes)), Decimal(len(track.volumes)))
            if len(track.volumes) == self._p.vol_window
            else None
        )
        atr = track.atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        track.volumes.append(bar.volume)

        if not live or atr is None or prior_avg_volume is None:
            return
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held)
        elif track.entries < self._p.max_entries and self._entries_open(bar):
            self._maybe_enter(track, bar, atr, prior_avg_volume)

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
        self, track: _Track, bar: Candle, atr: Decimal, prior_avg_volume: Decimal
    ) -> None:
        high, low, close = bar.high.amount, bar.low.amount, bar.close.amount
        span = ar.sub(high, low)
        if span <= ar.ZERO or close <= ar.ZERO:
            return
        if Decimal(bar.volume) < ar.mul(prior_avg_volume, self._p.vol_surge_mult):
            return
        if ar.div(ar.mul(span, _BPS), close) < self._p.min_range_bps:
            return
        close_location = ar.div(ar.sub(close, low), span)
        stop_distance = ar.mul(atr, self._p.stop_atr_mult)
        target_distance = ar.mul(atr, self._p.target_atr_mult)
        if close_location >= self._p.close_location_min:
            side, stop, target = OrderSide.BUY, close - stop_distance, close + target_distance
        elif self._p.allow_short and close_location <= ar.sub(ar.ONE, self._p.close_location_min):
            side, stop, target = OrderSide.SELL, close + stop_distance, close - target_distance
        else:
            return
        if self._enter(
            bar,
            side,
            f"volume {bar.volume} >= {self._p.vol_surge_mult}x the "
            f"{self._p.vol_window}-bar average ({prior_avg_volume:.0f}), close at "
            f"{close_location:.0%} of the bar's range",
        ):
            track.entries += 1
            track.stop, track.target = stop, target
