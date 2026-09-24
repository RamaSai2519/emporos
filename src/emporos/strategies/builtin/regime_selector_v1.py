"""regime_selector_v1 (EM-126) — route to a trend-following or mean-reversion rule by the
instrument's own daily-bar market regime, sharing the exact classifier the EM-119 breakdowns use.

The regime read is a genuinely separate signal from anything the 5m entry rules see: it comes from
`ctx.history`'s `Timeframe.D1` bars (fed by the engine/worker alongside the strategy's own
timeframe), classified with the SAME `MarketRegimeClassifier` used to tag trades in the metrics
report — never tuned differently for "what a strategy sees" versus "how a report is read". A new
daily bar is picked up the first 5m bar after it closes (whichever bar first sees it in
`ctx.history`); the read for TODAY is always YESTERDAY's regime, since today's own daily bar has
not closed yet — the same look-ahead-safety rule `RegimeTimeline` observes for reporting.

TRENDING routes to a channel-breakout entry (donchian_v1's idea, but only ever live in a trending
regime); RANGING routes to a VWAP mean-reversion entry (vwap_reversion_v1's idea, restricted the
same way). HIGH_VOL and LOW_VOL sit out entirely — extremes are not what either sub-rule assumes.
Both sub-rules use a fixed ATR stop/target (no trailing) and at most one entry per instrument per
day, the same discipline that kept squeeze_breakout_v1's trade count sane.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from pydantic import model_validator

from emporos.domain.candles import Candle, Timeframe
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
from emporos.strategies.intraday import DayTrack, IntradayStrategy, SessionVwap
from emporos.strategies.regime import MarketRegime, MarketRegimeClassifier

_BPS = Decimal(10_000)
_DAILY_LOOKBACK = 300  # bars read from ctx.history each check; classifier warm-up needs far fewer


class RegimeSelectorParameters(StrategyParameters):
    # the shared daily-bar regime classifier (defaults match MarketRegimeClassifier's own)
    regime_trend_period: PositiveInt = 20
    regime_atr_period: PositiveInt = 14
    regime_vol_window: PositiveInt = 60
    regime_trend_threshold: ExactDecimal = Decimal("0.4")
    regime_high_vol_percentile: ExactDecimal = Decimal("0.8")
    regime_low_vol_percentile: ExactDecimal = Decimal("0.2")
    # trend-following sub-rule (5m channel breakout, live only when TRENDING)
    breakout_lookback: PositiveInt = 10
    breakout_buffer_bps: ExactDecimal = Decimal(5)
    trend_stop_atr_mult: ExactDecimal = Decimal("1.0")
    trend_target_atr_mult: ExactDecimal = Decimal("2.0")
    # mean-reversion sub-rule (5m VWAP extension, live only when RANGING)
    reversion_atr_mult: ExactDecimal = Decimal("2.0")  # how far from VWAP counts as "extended"
    reversion_target_atr_mult: ExactDecimal = Decimal("1.0")
    reversion_stop_atr_mult: ExactDecimal = Decimal("1.0")
    atr_period: PositiveInt = 14  # the 5m ATR both sub-rules size stops/targets from
    allow_short: bool = True
    max_entries: PositiveInt = 1

    @model_validator(mode="after")
    def _consistent(self) -> RegimeSelectorParameters:
        if not 0 < self.regime_low_vol_percentile < self.regime_high_vol_percentile < 1:
            raise ValueError("0 < regime_low_vol_percentile < regime_high_vol_percentile < 1")
        multiples = (
            self.trend_stop_atr_mult, self.trend_target_atr_mult, self.reversion_atr_mult,
            self.reversion_target_atr_mult, self.reversion_stop_atr_mult,
        )  # fmt: skip
        if any(m <= 0 for m in multiples) or self.breakout_buffer_bps < 0:
            raise ValueError("ATR multiples must be positive, the buffer non-negative")
        return self


@dataclass
class _Track(DayTrack):
    classifier: MarketRegimeClassifier | None = None
    last_daily_ts: datetime | None = None
    atr: AverageTrueRange | None = None
    highs: deque[Decimal] = field(default_factory=deque)
    lows: deque[Decimal] = field(default_factory=deque)
    entries: int = 0
    stop: Decimal | None = None
    target: Decimal | None = None


class RegimeSelectorV1(IntradayStrategy):
    name: ClassVar[str] = "regime_selector_v1"
    parameters_model: ClassVar[type[StrategyParameters]] = RegimeSelectorParameters

    def __init__(self, config: ResolvedStrategyConfig) -> None:
        if not isinstance(config.parameters, RegimeSelectorParameters):
            raise TypeError("regime_selector_v1 needs RegimeSelectorParameters")
        self._p: RegimeSelectorParameters = config.parameters
        super().__init__(config)

    def _new_track(self) -> _Track:
        p = self._p
        return _Track(
            classifier=MarketRegimeClassifier(
                p.regime_trend_period,
                p.regime_atr_period,
                p.regime_vol_window,
                p.regime_trend_threshold,
                p.regime_high_vol_percentile,
                p.regime_low_vol_percentile,
            ),  # fmt: skip
            atr=AverageTrueRange(p.atr_period),
            vwap=SessionVwap(),
        )

    def _observe(self, track: DayTrack, bar: Candle, live: bool) -> None:
        assert isinstance(track, _Track) and track.atr is not None and track.classifier is not None
        if track.start_bar(bar):
            track.highs.clear()
            track.lows.clear()
            track.entries, track.stop, track.target = 0, None, None
        track.vwap.update(bar)
        regime = self._advance_regime(track, bar.instrument_id)

        channel_high = max(track.highs) if len(track.highs) == self._p.breakout_lookback else None
        channel_low = min(track.lows) if len(track.lows) == self._p.breakout_lookback else None
        close = bar.close.amount
        atr = track.atr.update(bar.high.amount, bar.low.amount, close)
        track.highs.append(bar.high.amount)
        track.lows.append(bar.low.amount)
        if len(track.highs) > self._p.breakout_lookback:
            track.highs.popleft()
            track.lows.popleft()

        if not live or atr is None or regime is None:
            return
        held = self._held(bar)
        if not held.is_flat:
            self._manage(track, bar, held)
            return
        if track.entries >= self._p.max_entries or not self._entries_open(bar):
            return
        if regime is MarketRegime.TRENDING and channel_high is not None and channel_low is not None:
            self._maybe_enter_trend(track, bar, atr, channel_high, channel_low)
        elif regime is MarketRegime.RANGING and track.vwap.value is not None:
            self._maybe_enter_reversion(track, bar, atr, track.vwap.value)

    def _advance_regime(self, track: _Track, instrument_id: str) -> MarketRegime | None:
        """Feeds every daily bar `ctx.history` has revealed since the last one this track saw,
        oldest first, into the classifier -- causal, since `bars()` only ever reveals a day once
        it has actually closed."""
        assert track.classifier is not None
        daily = self._require_ctx().history.bars(instrument_id, Timeframe.D1, _DAILY_LOOKBACK)
        new_bars = (
            daily if track.last_daily_ts is None
            else [d for d in daily if d.ts > track.last_daily_ts]
        )  # fmt: skip
        for day_bar in new_bars:
            track.classifier.update(day_bar)
            track.last_daily_ts = day_bar.ts
        return track.classifier.value

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

    def _maybe_enter_trend(
        self, track: _Track, bar: Candle, atr: Decimal, high: Decimal, low: Decimal
    ) -> None:
        close = bar.close.amount
        buffer = self._p.breakout_buffer_bps / _BPS
        stop_distance = ar.mul(atr, self._p.trend_stop_atr_mult)
        target_distance = ar.mul(atr, self._p.trend_target_atr_mult)
        if close > high * (1 + buffer):
            side, stop, target = OrderSide.BUY, close - stop_distance, close + target_distance
        elif self._p.allow_short and close < low * (1 - buffer):
            side, stop, target = OrderSide.SELL, close + stop_distance, close - target_distance
        else:
            return
        if self._enter(
            bar, side, f"trending: broke the {self._p.breakout_lookback}-bar range", stop=stop
        ):
            track.entries += 1
            track.stop, track.target = stop, target

    def _maybe_enter_reversion(
        self, track: _Track, bar: Candle, atr: Decimal, vwap: Decimal
    ) -> None:
        close = bar.close.amount
        extension = ar.mul(atr, self._p.reversion_atr_mult)
        stop_distance = ar.mul(atr, self._p.reversion_stop_atr_mult)
        target_distance = ar.mul(atr, self._p.reversion_target_atr_mult)
        if close <= vwap - extension:
            side, stop = OrderSide.BUY, close - stop_distance
            target = min(vwap, close + target_distance)
        elif self._p.allow_short and close >= vwap + extension:
            side, stop = OrderSide.SELL, close + stop_distance
            target = max(vwap, close - target_distance)
        else:
            return
        if self._enter(bar, side, "ranging: extended from VWAP", stop=stop):
            track.entries += 1
            track.stop, track.target = stop, target
