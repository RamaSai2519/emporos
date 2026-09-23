"""Cross-sectional factor construction for residual momentum research (EM-179).

A stock's raw return mixes three things: the market's move, its sector's move, and whatever is
left over — the part attributable to the stock itself. "Residual momentum" ranks stocks by that
leftover, not the raw return, because a stock that merely moved with the market is not exhibiting
its own momentum. This module builds the market and sector factor return series the rest of the
package neutralizes against, and estimates each instrument's loadings (betas) on them from
strictly prior/contemporaneous bar returns — the same "no code path may see the future" discipline
as `emporos.research.features.CausalHistory`, applied to a rolling regression instead of a single
value.

This project ingests no separate NIFTY index feed — plan.md's ingestion is per-instrument cash
candles only — so the market factor here is the study universe's OWN equal-weighted bar-return
series, a standard proxy when a true benchmark series is not available, not a stand-in for real
NIFTY constituents and weights. Likewise there is no real sector taxonomy in this codebase yet
(`emporos.research.regimes.SectorLookup` already exists for exactly this reason, shared by both
modules); a `SectorLookup` that classifies everything as `None` degrades sector-neutralization to
a no-op (pure market-neutralization), not an error.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.domain.candles import Candle
from emporos.research.horizons import Horizon
from emporos.research.regimes import SectorLookup

_ZERO = Decimal(0)
_ONE = Decimal(1)


def _defined(values: Sequence[Decimal | None]) -> list[Decimal]:
    return [v for v in values if v is not None]


class AlignedUniverse:
    """A study's bars, position-aligned across instruments: `bars_of(i)[index]` for every
    instrument is the same closed timestamp. Fetching genuinely aligned bars (same date range,
    same timeframe, one shared NSE/BSE trading calendar) is the composition root's job; an
    instrument whose bars do not align position-for-position with the rest is refused here rather
    than silently padded or reindexed — an instrument that was halted or newly listed mid-study
    simply does not belong in this universe for this run."""

    def __init__(self, bars_by_instrument: Mapping[str, Sequence[Candle]]) -> None:
        if not bars_by_instrument:
            raise ValueError("a universe needs at least one instrument")
        self._instrument_ids = tuple(sorted(bars_by_instrument))
        lengths = {len(bars_by_instrument[i]) for i in self._instrument_ids}
        if lengths == {0}:
            raise ValueError("a universe needs at least one bar")
        if len(lengths) != 1:
            raise ValueError("every instrument needs the same number of bars")
        reference = [c.ts for c in bars_by_instrument[self._instrument_ids[0]]]
        for instrument_id in self._instrument_ids[1:]:
            if [c.ts for c in bars_by_instrument[instrument_id]] != reference:
                raise ValueError(f"{instrument_id}'s bars are not position-aligned with the rest")
        self._bars = {i: tuple(bars_by_instrument[i]) for i in self._instrument_ids}
        self._length = len(reference)

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return self._instrument_ids

    @property
    def length(self) -> int:
        return self._length

    def bars_of(self, instrument_id: str) -> tuple[Candle, ...]:
        return self._bars[instrument_id]


def bar_returns(bars: Sequence[Candle]) -> list[Decimal | None]:
    """Close-to-close return at every bar; `None` at index 0 (no prior close) and wherever the
    prior close is zero."""
    returns: list[Decimal | None] = [None]
    for i in range(1, len(bars)):
        previous = bars[i - 1].close.amount
        if previous == _ZERO:
            returns.append(None)
        else:
            returns.append(DecimalMath.divide(bars[i].close.amount - previous, previous))
    return returns


def compounded_return(
    returns: Sequence[Decimal | None], start: int, end: int
) -> Decimal | None:
    """The compounded return spanning bars `start+1..end` of a per-bar return series — the
    trailing counterpart of `emporos.research.horizons.ForwardReturnCalculator`'s close-to-close
    math, built by compounding period returns rather than dividing closes, since a factor series
    (the market or a sector) has no single tradable price to divide. `None` if the span runs
    before the start of the series, past its end, or crosses any undefined bar."""
    if start < 0 or end >= len(returns) or end <= start:
        return None
    window = returns[start + 1 : end + 1]
    values: list[Decimal] = []
    for r in window:
        if r is None:
            return None
        values.append(r)
    product = _ONE
    for value in values:
        product *= _ONE + value
    return product - _ONE


class MarketFactor:
    """The study universe's own equal-weighted bar-return series, standing in for a true NIFTY
    series this codebase does not ingest (see the module docstring)."""

    def __init__(self, universe: AlignedUniverse) -> None:
        per_instrument = [bar_returns(universe.bars_of(i)) for i in universe.instrument_ids]
        series: list[Decimal | None] = []
        for index in range(universe.length):
            samples = _defined([r[index] for r in per_instrument])
            series.append(DecimalMath.mean(samples) if samples else None)
        self.returns: list[Decimal | None] = series


class SectorFactor:
    """Equal-weighted bar-return series per sector the injected `SectorLookup` names.
    Instruments with no known sector (`sector_of` returning `None`) contribute to no sector
    series and get `None` back from `returns_for`."""

    def __init__(self, universe: AlignedUniverse, sectors: SectorLookup) -> None:
        by_sector: dict[str, list[str]] = defaultdict(list)
        self._sector_of: dict[str, str | None] = {}
        for instrument_id in universe.instrument_ids:
            sector = sectors.sector_of(instrument_id)
            self._sector_of[instrument_id] = sector
            if sector is not None:
                by_sector[sector].append(instrument_id)
        per_instrument = {i: bar_returns(universe.bars_of(i)) for i in universe.instrument_ids}
        self._returns: dict[str, list[Decimal | None]] = {}
        for sector, members in by_sector.items():
            series: list[Decimal | None] = []
            for index in range(universe.length):
                samples = _defined([per_instrument[m][index] for m in members])
                series.append(DecimalMath.mean(samples) if samples else None)
            self._returns[sector] = series

    def returns_for(self, instrument_id: str) -> list[Decimal | None] | None:
        sector = self._sector_of.get(instrument_id)
        return None if sector is None else self._returns[sector]


def _ols_beta(xs: Sequence[Decimal], ys: Sequence[Decimal]) -> Decimal | None:
    """Single-factor OLS slope of `ys` on `xs`; `None` when `xs` has no variance to regress on."""
    mean_x, mean_y = DecimalMath.mean(xs), DecimalMath.mean(ys)
    covariance = sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)), _ZERO)
    variance_x = sum(((x - mean_x) ** 2 for x in xs), _ZERO)
    if variance_x == _ZERO:
        return None
    return DecimalMath.divide(covariance, variance_x)


def _two_factor_ols(
    triples: Sequence[tuple[Decimal, Decimal, Decimal]],
) -> tuple[Decimal, Decimal] | None:
    """Joint OLS loadings of `y` on `(x1, x2)`, solved from the 2x2 normal equations; `None` when
    the two regressors are collinear (or either is constant) over the window."""
    x1 = [t[0] for t in triples]
    x2 = [t[1] for t in triples]
    y = [t[2] for t in triples]
    m1, m2, my = DecimalMath.mean(x1), DecimalMath.mean(x2), DecimalMath.mean(y)
    d1 = [v - m1 for v in x1]
    d2 = [v - m2 for v in x2]
    dy = [v - my for v in y]
    s11 = sum((a * a for a in d1), _ZERO)
    s22 = sum((a * a for a in d2), _ZERO)
    s12 = sum((a * b for a, b in zip(d1, d2, strict=True)), _ZERO)
    s1y = sum((a * b for a, b in zip(d1, dy, strict=True)), _ZERO)
    s2y = sum((a * b for a, b in zip(d2, dy, strict=True)), _ZERO)
    determinant = s11 * s22 - s12 * s12
    if determinant == _ZERO:
        return None
    beta1 = DecimalMath.divide(s1y * s22 - s2y * s12, determinant)
    beta2 = DecimalMath.divide(s2y * s11 - s1y * s12, determinant)
    return beta1, beta2


class BetaEstimator:
    """Trailing OLS loadings on the market factor and, when available, a sector factor —
    estimated from a window of strictly prior/contemporaneous bar returns (this bar's own
    realized return is available by the time it closes, so including it is not looking ahead).
    Uses a joint two-factor regression when enough paired sector samples exist in the window,
    falling back to single-factor market-only (sector loading 0) otherwise — an instrument with
    no known sector, or a window where the two factors are collinear, simply carries no
    sector-neutralization term, not an error."""

    def __init__(self, window: int = 60, min_samples: int | None = None) -> None:
        if window < 2:
            raise ValueError("a beta window needs at least two bars")
        self._window = window
        self._min_samples = min_samples if min_samples is not None else max(2, window // 2)

    def loadings_at(
        self,
        index: int,
        instrument_returns: Sequence[Decimal | None],
        market_returns: Sequence[Decimal | None],
        sector_returns: Sequence[Decimal | None] | None,
    ) -> tuple[Decimal, Decimal] | None:
        start = index - self._window + 1
        if start < 0:
            return None
        window_slice = range(start, index + 1)
        if sector_returns is not None:
            triples: list[tuple[Decimal, Decimal, Decimal]] = []
            for i in window_slice:
                market, sector = market_returns[i], sector_returns[i]
                instrument = instrument_returns[i]
                if market is not None and sector is not None and instrument is not None:
                    triples.append((market, sector, instrument))
            if len(triples) >= self._min_samples:
                solved = _two_factor_ols(triples)
                if solved is not None:
                    return solved
        pairs: list[tuple[Decimal, Decimal]] = []
        for i in window_slice:
            market, instrument = market_returns[i], instrument_returns[i]
            if market is not None and instrument is not None:
                pairs.append((market, instrument))
        if len(pairs) < self._min_samples:
            return None
        beta_market = _ols_beta([p[0] for p in pairs], [p[1] for p in pairs])
        return None if beta_market is None else (beta_market, _ZERO)


class ResidualSignalCalculator:
    """The ranking signal for one instrument at one bar: its trailing `signal_horizon` return,
    with the market's and (if known) its sector's trailing return over the same span removed at
    the loadings `BetaEstimator` estimated from strictly prior data."""

    def __init__(self, beta_estimator: BetaEstimator) -> None:
        self._beta_estimator = beta_estimator

    def signal_at(
        self,
        index: int,
        signal_horizon: Horizon,
        instrument_returns: Sequence[Decimal | None],
        market_returns: Sequence[Decimal | None],
        sector_returns: Sequence[Decimal | None] | None,
    ) -> Decimal | None:
        origin = index - signal_horizon.bars
        trailing_instrument = compounded_return(instrument_returns, origin, index)
        if trailing_instrument is None:
            return None
        loadings = self._beta_estimator.loadings_at(
            index, instrument_returns, market_returns, sector_returns
        )
        if loadings is None:
            return None
        beta_market, beta_sector = loadings
        trailing_market = compounded_return(market_returns, origin, index)
        if trailing_market is None:
            return None
        residual = trailing_instrument - beta_market * trailing_market
        if sector_returns is not None:
            trailing_sector = compounded_return(sector_returns, origin, index)
            if trailing_sector is not None:
                residual -= beta_sector * trailing_sector
        return residual
