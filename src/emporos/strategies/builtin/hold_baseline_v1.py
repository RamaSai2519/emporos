"""hold_baseline_v1 — the simplest thing to beat: long every instrument from the open to the close.

It has no view. The first bar of the day it can trade, it buys; the session's square-off flattens
it. It exists so a strategy's result can be set against "just being long intraday" through the very
same engine, risk rules and charges (EM-118), not so it can ever trade: its shipped config is
never enabled, and it is never a candidate for curation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.strategies.config import ResolvedStrategyConfig, StrategyParameters
from emporos.strategies.intraday import DayTrack, IntradayStrategy


class HoldBaselineParameters(StrategyParameters):
    """Nothing to tune: a baseline with parameters would be a strategy."""


@dataclass
class _HoldTrack(DayTrack):
    bought: bool = False


class HoldBaselineV1(IntradayStrategy):
    name: ClassVar[str] = "hold_baseline_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = HoldBaselineParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, HoldBaselineParameters):
            raise TypeError("hold_baseline_v1 needs HoldBaselineParameters")
        super().__init__(config)

    def _new_track(self) -> _HoldTrack:
        return _HoldTrack()

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _HoldTrack)
        if track.start_bar(bar):
            track.bought = False
        if not live or track.bought or not self._entries_open(bar) or not self._held(bar).is_flat:
            return
        track.bought = self._enter(bar, OrderSide.BUY, "baseline: long from the open to the close")
