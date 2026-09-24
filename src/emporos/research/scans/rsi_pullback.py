"""The `rsi_pullback_v1` rules as a scan (EM-191 F3b): buy a sharp pullback in an uptrend.

Mirrors `strategies.builtin.rsi_pullback_v1` where it decides.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from emporos.domain.candles import Candle
from emporos.domain.orders import OrderSide
from emporos.research.scans.base import (
    EntryIntent,
    ExitIntent,
    IntradayScan,
    ScanExecution,
    ScanIntent,
    entries_open,
)
from emporos.strategies.builtin.rsi_pullback_v1 import RsiPullbackParameters
from emporos.strategies.indicators import (
    AverageTrueRange,
    ExponentialMovingAverage,
    RelativeStrengthIndex,
)
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.intraday import DayTrack

__all__ = ["RsiPullbackRules", "rsi_pullback_scan"]


class RsiPullbackRules:
    def __init__(self, parameters: RsiPullbackParameters, no_new_entries_after: time) -> None:
        self._p = parameters
        self._cutoff = no_new_entries_after
        self._track = DayTrack()
        self._atr = AverageTrueRange(parameters.atr_period)
        self._rsi = RelativeStrengthIndex(parameters.rsi_period)
        self._trend = ExponentialMovingAverage(parameters.trend_ema)
        self._entered_bar: int | None = None
        self._stop: Decimal | None = None

    def observe(
        self, bar: Candle, *, live: bool, held: OrderSide | None, can_afford: bool
    ) -> ScanIntent:
        track = self._track
        if track.start_bar(bar):
            self._entered_bar, self._stop = None, None
        atr = self._atr.update(bar.high.amount, bar.low.amount, bar.close.amount)
        rsi = self._rsi.update(bar.close.amount)
        trend = self._trend.update(bar.close.amount)
        if not live or atr is None or rsi is None or trend is None:
            return None
        if held is not None:
            return self._manage(bar, held, rsi)
        if track.bar_of_day > self._p.skip_bars and entries_open(bar, self._cutoff):
            return self._maybe_enter(bar, atr, rsi, trend, can_afford)
        return None

    def _manage(self, bar: Candle, held: OrderSide, rsi: Decimal) -> ScanIntent:
        close = bar.close.amount
        if self._entered_bar is None or self._stop is None:
            return None
        if held is OrderSide.BUY:
            reason = (
                "recovered" if rsi >= self._p.rsi_exit
                else "stopped" if close <= self._stop else None
            )  # fmt: skip
        else:
            reason = (
                "recovered" if rsi <= 100 - self._p.rsi_exit
                else "stopped" if close >= self._stop else None
            )  # fmt: skip
        if reason is None and self._track.bar_of_day - self._entered_bar >= self._p.max_hold_bars:
            reason = "held too long"
        if reason is None:
            return None
        self._entered_bar, self._stop = None, None
        return ExitIntent()

    def _maybe_enter(
        self, bar: Candle, atr: Decimal, rsi: Decimal, trend: Decimal, can_afford: bool
    ) -> ScanIntent:
        close = bar.close.amount
        distance = ar.mul(atr, self._p.stop_atr_mult)
        if close > trend and rsi <= self._p.rsi_entry:
            side, stop = OrderSide.BUY, close - distance
        elif self._p.allow_short and close < trend and rsi >= 100 - self._p.rsi_entry:
            side, stop = OrderSide.SELL, close + distance
        else:
            return None
        if not can_afford:
            return None
        self._entered_bar, self._stop = self._track.bar_of_day, stop
        return EntryIntent(side)


def rsi_pullback_scan(parameters: RsiPullbackParameters, execution: ScanExecution) -> IntradayScan:
    return IntradayScan(
        "rsi_pullback_v1",
        lambda: RsiPullbackRules(parameters, execution.no_new_entries_after),
        execution,
    )
