"""Trade-sequence Monte Carlo: how much of a result is the order and the luck of its trades?

Two questions, two resamplers, both over the trades a run closed (nothing else is read):

* PERMUTATION keeps every trade and shuffles the order. Net P&L cannot change, but the path does,
  so it answers "how deep could the drawdown have been" and "how often would this order have
  breached the drawdown limit" (the probability of ruin).
* BOOTSTRAP draws the same number of trades with replacement. It answers "how far could net P&L,
  expectancy and profit factor have moved had we met a different mix of the same trades", as a
  confidence interval and the probability that the run was net positive.

Too few trades make a resampled interval look tight for the wrong reason, so under `min_trades`
the report carries the observed figures and a reason, and no distribution at all.

Randomness comes from an injected `RandomSource`; the default is seeded, so the same trades and
seed give the same report on every machine. Arithmetic is `Decimal` under the metrics' fixed
context. Percentiles are nearest-rank, so every reported value is a value that occurred.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Protocol

from emporos.backtest.metrics.decimal_math import ONE, ZERO, DecimalMath
from emporos.backtest.portfolio import ClosedTrade

DEFAULT_RESAMPLES = 5000
DEFAULT_MIN_TRADES = 30
_TWO = Decimal(2)


class RandomSource(Protocol):
    def below(self, bound: int) -> int:
        """An integer in [0, bound)."""
        ...


class SeededSource:
    """The default source: Python's Mersenne Twister, whose `randrange` is stable across runs."""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def below(self, bound: int) -> int:
        return self._random.randrange(bound)


@dataclass(frozen=True)
class TradeOutcome:
    """What a trade contributes to any resampled path: its net money and its return on notional."""

    net: Decimal
    net_return: Decimal

    @classmethod
    def of(cls, trade: ClosedTrade) -> TradeOutcome:
        notional = trade.entry_notional.amount
        if notional <= ZERO:
            raise ValueError("a trade must have entered a positive notional")
        return cls(trade.net_pnl.amount, DecimalMath.divide(trade.net_pnl.amount, notional))


class Resampler(Protocol):
    def draw(
        self, outcomes: Sequence[TradeOutcome], source: RandomSource
    ) -> list[TradeOutcome]: ...


class Permutation:
    """The same trades in a random order (Fisher-Yates)."""

    def draw(self, outcomes: Sequence[TradeOutcome], source: RandomSource) -> list[TradeOutcome]:
        items = list(outcomes)
        for i in range(len(items) - 1, 0, -1):
            j = source.below(i + 1)
            items[i], items[j] = items[j], items[i]
        return items


class Bootstrap:
    """As many trades as the run had, each drawn with replacement."""

    def draw(self, outcomes: Sequence[TradeOutcome], source: RandomSource) -> list[TradeOutcome]:
        return [outcomes[source.below(len(outcomes))] for _ in outcomes]


@dataclass(frozen=True)
class MonteCarloConfig:
    seed: int
    resamples: int = DEFAULT_RESAMPLES
    confidence: Decimal = Decimal("0.95")
    starting_equity: Decimal = Decimal(50000)
    drawdown_limit: Decimal = Decimal("0.10")  # ruin = a drawdown of at least this fraction
    min_trades: int = DEFAULT_MIN_TRADES

    def __post_init__(self) -> None:
        if self.resamples < 1:
            raise ValueError("resamples must be at least 1")
        if not ZERO < self.confidence < ONE:
            raise ValueError("confidence must be strictly between 0 and 1")
        if self.starting_equity <= ZERO:
            raise ValueError("starting equity must be positive")
        if not ZERO < self.drawdown_limit <= ONE:
            raise ValueError("the drawdown limit must be a fraction in (0, 1]")
        if self.min_trades < 1:
            raise ValueError("min_trades must be at least 1")


@dataclass(frozen=True)
class Interval:
    observed: Decimal | None  # None for a profit factor the run itself could not compute
    low: Decimal
    high: Decimal


@dataclass(frozen=True)
class ObservedFigures:
    net_pnl: Decimal
    expectancy: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal


@dataclass(frozen=True)
class ResampledDistribution:
    net_pnl: Interval  # bootstrap
    expectancy: Interval  # bootstrap
    profit_factor: Interval | None  # bootstrap; None when no resample had a loss to divide by
    profit_factor_undefined: int  # bootstrap resamples with no losing trade
    probability_net_positive: Decimal  # bootstrap
    max_drawdown: Interval  # permutation
    probability_of_ruin: Decimal  # permutation: share of orderings that reach the drawdown limit


@dataclass(frozen=True)
class MonteCarloReport:
    trade_count: int
    resamples: int
    seed: int
    confidence: Decimal
    observed: ObservedFigures
    distribution: ResampledDistribution | None
    inconclusive_reason: str | None

    @property
    def conclusive(self) -> bool:
        return self.distribution is not None


class PathStatistics:
    """The figures read off one ordered sequence of trades, from a known starting equity."""

    def __init__(self, starting_equity: Decimal) -> None:
        self._start = starting_equity

    @staticmethod
    def net(path: Sequence[TradeOutcome]) -> Decimal:
        return sum((o.net for o in path), ZERO)

    @staticmethod
    def expectancy(path: Sequence[TradeOutcome]) -> Decimal | None:
        return DecimalMath.mean([o.net_return for o in path]) if path else None

    @staticmethod
    def profit_factor(path: Sequence[TradeOutcome]) -> Decimal | None:
        profit = sum((o.net for o in path if o.net > ZERO), ZERO)
        loss = -sum((o.net for o in path if o.net < ZERO), ZERO)
        return None if loss == ZERO else DecimalMath.divide(profit, loss)

    def max_drawdown(self, path: Sequence[TradeOutcome]) -> Decimal:
        equity = peak = self._start
        deepest = ZERO
        for outcome in path:
            equity += outcome.net
            peak = max(peak, equity)
            deepest = max(deepest, DecimalMath.divide(peak - equity, peak))
        return deepest


class Percentiles:
    """Nearest-rank percentiles: the smallest value with at least `q` of the sample at or below."""

    @staticmethod
    def of(ordered: Sequence[Decimal], q: Decimal) -> Decimal:
        rank = int((q * len(ordered)).to_integral_value(rounding=ROUND_CEILING))
        return ordered[min(max(rank, 1), len(ordered)) - 1]

    @classmethod
    def interval(
        cls, observed: Decimal | None, sample: Sequence[Decimal], confidence: Decimal
    ) -> Interval:
        ordered = sorted(sample)
        tail = (ONE - confidence) / _TWO
        return Interval(observed, cls.of(ordered, tail), cls.of(ordered, ONE - tail))


class MonteCarlo:
    def __init__(
        self,
        config: MonteCarloConfig,
        permutation: Resampler | None = None,
        bootstrap: Resampler | None = None,
        source_factory: Callable[[int], RandomSource] = SeededSource,
    ) -> None:
        self._config = config
        self._permutation = permutation or Permutation()
        self._bootstrap = bootstrap or Bootstrap()
        self._source_factory = source_factory
        self._stats = PathStatistics(config.starting_equity)

    def run(self, trades: Sequence[ClosedTrade]) -> MonteCarloReport:
        ordered = sorted(trades, key=lambda t: (t.closed_at, t.opened_at, t.instrument_id))
        path = [TradeOutcome.of(t) for t in ordered]
        observed = ObservedFigures(
            self._stats.net(path),
            self._stats.expectancy(path),
            self._stats.profit_factor(path),
            self._stats.max_drawdown(path),
        )
        reason = self._too_few(len(path))
        distribution = None if reason else self._resample(path, observed)
        return MonteCarloReport(
            len(path), self._config.resamples, self._config.seed, self._config.confidence,
            observed, distribution, reason,
        )  # fmt: skip

    def _too_few(self, count: int) -> str | None:
        if count >= self._config.min_trades:
            return None
        return (
            f"{count} trades is fewer than the {self._config.min_trades} needed for a resampled "
            "interval to mean anything"
        )

    def _resample(
        self, path: list[TradeOutcome], observed: ObservedFigures
    ) -> ResampledDistribution:
        source = self._source_factory(self._config.seed)
        drawdowns = [
            self._stats.max_drawdown(self._permutation.draw(path, source))
            for _ in range(self._config.resamples)
        ]
        boots = [self._bootstrap.draw(path, source) for _ in range(self._config.resamples)]
        nets = [self._stats.net(b) for b in boots]
        expectancies = [e for e in (self._stats.expectancy(b) for b in boots) if e is not None]
        factors = [f for f in (self._stats.profit_factor(b) for b in boots) if f is not None]
        confidence = self._config.confidence
        return ResampledDistribution(
            net_pnl=Percentiles.interval(observed.net_pnl, nets, confidence),
            expectancy=Percentiles.interval(observed.expectancy, expectancies, confidence),
            profit_factor=(
                Percentiles.interval(observed.profit_factor, factors, confidence)
                if factors
                else None
            ),
            profit_factor_undefined=len(boots) - len(factors),
            probability_net_positive=self._share(sum(1 for n in nets if n > ZERO), len(nets)),
            max_drawdown=Percentiles.interval(observed.max_drawdown, drawdowns, confidence),
            probability_of_ruin=self._share(
                sum(1 for d in drawdowns if d >= self._config.drawdown_limit), len(drawdowns)
            ),
        )

    @staticmethod
    def _share(part: int, whole: int) -> Decimal:
        return DecimalMath.divide(Decimal(part), Decimal(whole))
