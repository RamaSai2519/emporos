"""Segmentation axes for feature research (EM-178): volatility, liquidity, market structure and
sector — each a causal, per-bar classifier used only to GROUP evaluation outcomes in a report.
None of these feed a trading decision; `emporos.strategies.regime` remains the one thing used
live, and `MarketRegimeAxis` composes it rather than duplicating its math (open/closed).
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Protocol

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.domain.candles import Candle
from emporos.strategies.regime import MarketRegimeClassifier

LOW, MEDIUM, HIGH = "low", "medium", "high"


class RegimeAxis(Protocol):
    """One segmentation dimension: `update` reads only the bar just closed and what a concrete
    axis chooses to remember from bars before it."""

    @property
    def name(self) -> str: ...

    def update(self, candle: Candle) -> str | None:
        """This bar's label, or `None` while there is not yet enough history."""
        ...


class _PercentileBucket:
    """LOW/MEDIUM/HIGH by trailing percentile rank of a per-bar reading — the causal
    percentile-rank technique `MarketRegimeClassifier` uses for volatility, generalised to any
    Decimal reading so it can serve the volatility and liquidity axes alike."""

    def __init__(self, window: int, low: Decimal, high: Decimal) -> None:
        if window < 2:
            raise ValueError("a percentile bucket needs a window of at least two")
        if not (Decimal(0) <= low < high <= Decimal(1)):
            raise ValueError("percentile thresholds must satisfy 0 <= low < high <= 1")
        self._window = window
        self._low, self._high = low, high
        self._history: deque[Decimal] = deque(maxlen=window)

    def classify(self, reading: Decimal) -> str | None:
        self._history.append(reading)
        if len(self._history) < self._window:
            return None
        at_or_below = sum(1 for sample in self._history if sample <= reading)
        percentile = DecimalMath.divide(Decimal(at_or_below), Decimal(len(self._history)))
        if percentile >= self._high:
            return HIGH
        if percentile <= self._low:
            return LOW
        return MEDIUM


class VolatilityBucket:
    """Trailing percentile rank of the bar's true range as a fraction of its close."""

    name = "volatility"

    def __init__(
        self, window: int = 60, low: Decimal = Decimal("0.33"), high: Decimal = Decimal("0.67")
    ) -> None:
        self._bucket = _PercentileBucket(window, low, high)
        self._previous_close: Decimal | None = None

    def update(self, candle: Candle) -> str | None:
        high, low, close = candle.high.amount, candle.low.amount, candle.close.amount
        true_range = high - low
        if self._previous_close is not None:
            true_range = max(
                true_range, abs(high - self._previous_close), abs(low - self._previous_close)
            )
        self._previous_close = close
        if close == Decimal(0):
            return None
        return self._bucket.classify(DecimalMath.divide(true_range, close))


class LiquidityBucket:
    """Trailing percentile rank of traded volume against the SAME instrument's own recent
    history — a proxy, not a cross-sectional ADV rank (this codebase has no point-in-time
    universe-wide volume series to rank against yet; EM-181's order-flow work may add one)."""

    name = "liquidity"

    def __init__(
        self, window: int = 60, low: Decimal = Decimal("0.33"), high: Decimal = Decimal("0.67")
    ) -> None:
        self._bucket = _PercentileBucket(window, low, high)

    def update(self, candle: Candle) -> str | None:
        return self._bucket.classify(Decimal(candle.volume))


class SpreadProxyBucket:
    """Trailing percentile rank of the Amihud (2002) illiquidity ratio — `|return| / volume` —
    a standard proxy for effective bid-ask spread when no Level-1 quote data is available (this
    codebase ingests OHLCV candles only, no order book). HIGH = wide effective spread (illiquid,
    costly to trade); distinct from `VolatilityBucket` (range-based) and `LiquidityBucket`
    (raw volume): a bar can be volatile and heavily traded yet still cheap to cross, or quiet and
    thin yet expensive to cross."""

    name = "spread"

    def __init__(
        self, window: int = 60, low: Decimal = Decimal("0.33"), high: Decimal = Decimal("0.67")
    ) -> None:
        self._bucket = _PercentileBucket(window, low, high)
        self._previous_close: Decimal | None = None

    def update(self, candle: Candle) -> str | None:
        close, volume = candle.close.amount, candle.volume
        previous = self._previous_close
        self._previous_close = close
        if volume == 0:
            return None  # illiquidity is undefined for a bar with no trades, not zero
        move = (
            Decimal(0)
            if previous is None or previous == Decimal(0)
            else DecimalMath.divide(abs(close - previous), previous)
        )
        return self._bucket.classify(DecimalMath.divide(move, Decimal(volume)))


class MarketRegimeAxis:
    """Wraps the live `MarketRegimeClassifier` (trending/ranging/high_vol/low_vol) as a
    segmentation axis, so feature research groups by the SAME regime definition a strategy would
    see live, not a second, divergent one."""

    name = "market_regime"

    def __init__(self, classifier: MarketRegimeClassifier | None = None) -> None:
        self._classifier = classifier or MarketRegimeClassifier()

    def update(self, candle: Candle) -> str | None:
        regime = self._classifier.update(candle)
        return None if regime is None else regime.value


class SectorLookup(Protocol):
    """The one method a sector axis needs. Defined locally rather than importing
    `emporos.opportunity`'s classifier, per Interface Segregation — this consumer depends on
    nothing it does not call."""

    def sector_of(self, instrument_id: str) -> str | None: ...


class SectorAxis:
    """Sector is static per instrument, not derived from the bar."""

    name = "sector"

    def __init__(self, sectors: SectorLookup) -> None:
        self._sectors = sectors

    def update(self, candle: Candle) -> str | None:
        return self._sectors.sector_of(candle.instrument_id)
