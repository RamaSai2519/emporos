"""gap_fade_v1 — a big overnight gap that fails its opening range closes toward the old price.

A session that opens `min_gap_bps` or more (no more than `max_gap_bps`) away from the previous
close is a gap day. Once the opening range (`range_bars` bars) is done, the first bar that CLOSES
back through the range AGAINST the gap (below the range after a gap up, above it after a gap down)
is the entry, in that direction: the gap is failing. The target is `fill_fraction` of the way back
to the previous close, the stop is the far side of the opening range; both are checked on bar
closes and anything left is flattened by the session's square-off. One entry per instrument per
day. No market-direction filter: the platform holds no index bars (EM-124).
"""

from __future__ import annotations

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
from emporos.strategies.gaps import GapTrack
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack, IntradayStrategy


class GapFadeParameters(StrategyParameters):
    min_gap_bps: ExactDecimal = Decimal(100)
    max_gap_bps: ExactDecimal = Decimal(500)
    range_bars: PositiveInt = 3
    fill_fraction: ExactDecimal = Decimal("1.0")  # of the way back to the previous close
    allow_short: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> GapFadeParameters:
        if not 0 < self.min_gap_bps < self.max_gap_bps:
            raise ValueError("need 0 < min_gap_bps < max_gap_bps")
        if not 0 < self.fill_fraction <= 1:
            raise ValueError("fill_fraction must be in (0, 1]")
        return self


class GapFadeV1(IntradayStrategy):
    name: ClassVar[str] = "gap_fade_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = GapFadeParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, GapFadeParameters):
            raise TypeError("gap_fade_v1 needs GapFadeParameters")
        self._p: GapFadeParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> GapTrack:
        return GapTrack()

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, GapTrack)
        track.advance(bar, self._p.range_bars)
        if not live or track.bar_of_day <= self._p.range_bars or not track.range_ready:
            return
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held)
        elif not track.traded and self._entries_open(bar):
            self._maybe_enter(track, bar)

    def _manage(self, track: GapTrack, bar: Candle, held: Position) -> None:
        close = bar.close.amount
        if track.stop is None or track.target is None:
            return
        if held.is_long and (close <= track.stop or close >= track.target):
            self._exit(bar, held, f"long {'stopped' if close <= track.stop else 'gap filled'}")
        elif not held.is_long and (close >= track.stop or close <= track.target):
            self._exit(bar, held, f"short {'stopped' if close >= track.stop else 'gap filled'}")

    def _maybe_enter(self, track: GapTrack, bar: Candle) -> None:
        gap, previous = track.gap_bps, track.prev_close
        if gap is None or previous is None:
            return
        if not self._p.min_gap_bps <= abs(gap) <= self._p.max_gap_bps:
            return
        assert track.range_high is not None and track.range_low is not None
        close = bar.close.amount
        way_back = ar.mul(ar.sub(close, previous), self._p.fill_fraction)
        if gap > 0 and self._p.allow_short and close < track.range_low:
            side, stop, target = OrderSide.SELL, track.range_high, close - way_back
        elif gap < 0 and close > track.range_high:
            side, stop, target = OrderSide.BUY, track.range_low, close - way_back
        else:
            return
        if (side is OrderSide.SELL and target >= close) or (
            side is OrderSide.BUY and target <= close
        ):
            return  # already past the old price: nothing left to fade
        if self._enter(bar, side, f"gap {gap:.0f} bps failed its opening range", stop=stop):
            track.traded, track.stop, track.target = True, stop, target
