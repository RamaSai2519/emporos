"""Market regime classification (EM-119/EM-126): trending, ranging, high-vol or low-vol.

Look-ahead safe by construction: `MarketRegimeClassifier.update` is causal, like the indicators it
is built from (`AverageTrueRange`, `EfficiencyRatio`) — each call sees only the bar just closed and
bars seen before it. A regime read for a given bar never depends on anything that had not yet
happened. Versioned (`VERSION`) so a stored breakdown can record which classifier produced it; a
future revision ships as `v2`, not a silent change to `v1`'s numbers.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

from emporos.domain.candles import Candle
from emporos.strategies.indicators import arithmetic as ar
from emporos.strategies.indicators.atr import AverageTrueRange
from emporos.strategies.indicators.efficiency_ratio import EfficiencyRatio

DEFAULT_TREND_PERIOD = 20
DEFAULT_ATR_PERIOD = 14
DEFAULT_VOL_WINDOW = 60
DEFAULT_TREND_THRESHOLD = Decimal("0.4")
DEFAULT_HIGH_VOL_PERCENTILE = Decimal("0.8")
DEFAULT_LOW_VOL_PERCENTILE = Decimal("0.2")


class MarketRegime(StrEnum):
    TRENDING = "trending"
    RANGING = "ranging"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"


class MarketRegimeClassifier:
    """Classifies each closed bar into one `MarketRegime`, from two independent, causal reads:

    - volatility: today's ATR-as-a-fraction-of-price, ranked against its own trailing
      `vol_window` bars (scale-invariant across instruments priced from tens to thousands of
      rupees) — extreme readings (top/bottom `high_vol_percentile`/`low_vol_percentile`) dominate,
      since an extreme-volatility bar is its own regime regardless of trend structure.
    - trend: Kaufman's Efficiency Ratio over `trend_period` bars — at/above `trend_threshold` is
      TRENDING (price covered ground in one direction), otherwise RANGING.

    Ready once both reads have enough history: `atr_period + vol_window` bars for volatility,
    `trend_period + 1` for trend (volatility is almost always the binding constraint)."""

    VERSION: ClassVar[str] = "v1"

    def __init__(
        self,
        trend_period: int = DEFAULT_TREND_PERIOD,
        atr_period: int = DEFAULT_ATR_PERIOD,
        vol_window: int = DEFAULT_VOL_WINDOW,
        trend_threshold: Decimal = DEFAULT_TREND_THRESHOLD,
        high_vol_percentile: Decimal = DEFAULT_HIGH_VOL_PERCENTILE,
        low_vol_percentile: Decimal = DEFAULT_LOW_VOL_PERCENTILE,
    ) -> None:
        if vol_window < 1:
            raise ValueError("vol_window must be a positive integer")
        if not (ar.ZERO <= low_vol_percentile < high_vol_percentile <= ar.ONE):
            raise ValueError("percentile thresholds must satisfy 0 <= low < high <= 1")
        self._trend = EfficiencyRatio(trend_period)
        self._atr = AverageTrueRange(atr_period)
        self._vol_window = vol_window
        self._trend_threshold = trend_threshold
        self._high_vol_percentile = high_vol_percentile
        self._low_vol_percentile = low_vol_percentile
        self._atr_pct_history: deque[Decimal] = deque(maxlen=vol_window)
        self._value: MarketRegime | None = None

    @property
    def value(self) -> MarketRegime | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def update(self, candle: Candle) -> MarketRegime | None:
        close = candle.close.amount
        er = self._trend.update(close)
        atr = self._atr.update(candle.high.amount, candle.low.amount, close)
        if atr is None or close == ar.ZERO:
            return None
        atr_pct = ar.div(atr, close)
        self._atr_pct_history.append(atr_pct)
        if er is None or len(self._atr_pct_history) < self._vol_window:
            return None
        percentile = self._percentile_rank(atr_pct)
        if percentile >= self._high_vol_percentile:
            self._value = MarketRegime.HIGH_VOL
        elif percentile <= self._low_vol_percentile:
            self._value = MarketRegime.LOW_VOL
        elif er >= self._trend_threshold:
            self._value = MarketRegime.TRENDING
        else:
            self._value = MarketRegime.RANGING
        return self._value

    def _percentile_rank(self, current: Decimal) -> Decimal:
        at_or_below = sum(1 for sample in self._atr_pct_history if sample <= current)
        return ar.div(Decimal(at_or_below), Decimal(len(self._atr_pct_history)))
