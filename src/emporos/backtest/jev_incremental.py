"""What Jev added on top of an otherwise identical portfolio (EM-187).

`JevIncrementalAnalysis.of` turns one `JevPnlComparison` into `JevIncrementalEvidence`: for each
arm the metrics the ticket asks for (expectancy, Sharpe, Deflated Sharpe, max drawdown, turnover,
charges, trade count), the delta of each, Jev's own cost in rupees, the net-of-Jev delta, a paired
day-level bootstrap interval for whether the improvement is distinguishable from noise, and the
per-regime deltas so Jev cannot "help" only by trading one regime.

Conventions, chosen so the number a gate reads cannot flatter Jev:

* Jev's cost is `tokens / 1000 x inr_per_1k_tokens`, a rate that must be declared; an unpriced
  Jev is refused, never treated as free.
* "Net of Jev" subtracts that cost from the treatment arm. For the paired bootstrap it is spread
  evenly over the treatment arm's trading days (the run's token total has no per-day breakdown),
  which shifts the difference by exactly the daily cost and so cannot hide a loss.
* The Deflated Sharpe of each arm uses the trial statistics handed in, which must already count
  every Jev variant tried (the caller records them in the ledger first), so the search is priced.
* Sharpe and Deflated Sharpe are those of the arm's own daily returns, gross of the Jev cost; the
  cost enters through the net-of-Jev P&L and through the paired interval.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.jev_pnl import JevPnlComparison
from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.metrics.report import MetricsReport
from emporos.backtest.metrics.trades import TradeStatistics
from emporos.backtest.robustness.deflated_sharpe import DeflatedSharpe
from emporos.backtest.robustness.paired_bootstrap import PairedBootstrapReport, PairedDayBootstrap
from emporos.backtest.robustness.trials import TrialStatistics
from emporos.core.errors import ConfigurationError

_THOUSAND = Decimal(1000)


@dataclass(frozen=True)
class ArmMetrics:
    net_pnl: Decimal
    trade_count: int
    expectancy: Decimal | None  # mean return on notional per trade
    average_trade: Decimal | None  # mean net P&L per trade, in rupees
    sharpe: Decimal | None  # annualised
    deflated_sharpe: Decimal | None
    max_drawdown: Decimal  # a fraction of the peak
    turnover: Decimal | None
    charges: Decimal  # brokerage, statutory and impact charges together, in rupees


@dataclass(frozen=True)
class MetricDeltas:
    """Treatment minus baseline; None where either side has no value."""

    net_pnl: Decimal
    trade_count: int
    expectancy: Decimal | None
    sharpe: Decimal | None
    deflated_sharpe: Decimal | None
    max_drawdown: Decimal  # negative is an improvement
    turnover: Decimal | None
    charges: Decimal


@dataclass(frozen=True)
class RegimeDelta:
    baseline_count: int
    treatment_count: int
    net_pnl_delta: Decimal
    expectancy_delta: Decimal | None


@dataclass(frozen=True)
class JevCost:
    reviews: int
    rejections: int
    tokens: int
    latency_ms: int
    inr_per_1k_tokens: Decimal
    inr: Decimal


@dataclass(frozen=True)
class JevIncrementalEvidence:
    baseline: ArmMetrics
    treatment: ArmMetrics
    deltas: MetricDeltas
    cost: JevCost
    net_of_jev_pnl_delta: Decimal  # treatment net minus baseline net, minus what Jev cost
    net_of_jev_average_trade_delta: Decimal | None  # the same, per trade, in rupees
    paired: PairedBootstrapReport | None  # None when the arms cannot be paired; see `paired_reason`
    paired_reason: str | None
    by_regime: dict[str, RegimeDelta]
    trial_count: int
    fingerprint: str

    @property
    def improved_regimes(self) -> tuple[str, ...]:
        return tuple(sorted(r for r, d in self.by_regime.items() if d.net_pnl_delta > ZERO))

    @property
    def worsened_regimes(self) -> tuple[str, ...]:
        return tuple(sorted(r for r, d in self.by_regime.items() if d.net_pnl_delta < ZERO))


class JevIncrementalAnalysis:
    def __init__(self, bootstrap: PairedDayBootstrap, deflated: DeflatedSharpe | None = None):
        self._bootstrap = bootstrap
        self._deflated_override = deflated

    def of(
        self,
        comparison: JevPnlComparison,
        trials: TrialStatistics,
        inr_per_1k_tokens: Decimal | None,
    ) -> JevIncrementalEvidence:
        if inr_per_1k_tokens is None:
            raise ConfigurationError(
                "Jev's cost cannot be measured without a declared inr_per_1k_tokens: "
                "an unpriced Jev is not free"
            )
        base, treat = comparison.baseline.metrics, comparison.treatment.metrics
        jev = comparison.treatment.jev
        cost = JevCost(
            reviews=jev.reviews,
            rejections=jev.rejections,
            tokens=jev.tokens,
            latency_ms=jev.latency_ms,
            inr_per_1k_tokens=inr_per_1k_tokens,
            inr=DecimalMath.divide(Decimal(jev.tokens), _THOUSAND) * inr_per_1k_tokens,
        )
        baseline, treatment = self._arm(base, trials), self._arm(treat, trials)
        deltas = self._deltas(baseline, treatment)
        paired = self._paired(base, treat, cost.inr)
        return JevIncrementalEvidence(
            baseline=baseline,
            treatment=treatment,
            deltas=deltas,
            cost=cost,
            net_of_jev_pnl_delta=deltas.net_pnl - cost.inr,
            net_of_jev_average_trade_delta=self._average_trade_delta(baseline, treatment, cost),
            paired=None if isinstance(paired, str) else paired,
            paired_reason=paired if isinstance(paired, str) else None,
            by_regime=self._regimes(base, treat),
            trial_count=trials.count,
            fingerprint=comparison.fingerprint.digest,
        )

    def _arm(self, metrics: MetricsReport, trials: TrialStatistics) -> ArmMetrics:
        deflated = self._deflated_override or DeflatedSharpe(metrics.settings.annualisation_days)
        report = deflated.evaluate(metrics.daily_returns, trials)
        stats = metrics.trades
        return ArmMetrics(
            net_pnl=stats.net_pnl.amount,
            trade_count=stats.count,
            expectancy=stats.expectancy,
            average_trade=None if stats.average_trade is None else stats.average_trade.amount,
            sharpe=metrics.returns.sharpe,
            deflated_sharpe=report.deflated_sharpe,
            max_drawdown=metrics.drawdown.max_drawdown,
            turnover=metrics.turnover.turnover,
            charges=stats.fees.amount,
        )

    @staticmethod
    def _deltas(baseline: ArmMetrics, treatment: ArmMetrics) -> MetricDeltas:
        return MetricDeltas(
            net_pnl=treatment.net_pnl - baseline.net_pnl,
            trade_count=treatment.trade_count - baseline.trade_count,
            expectancy=_difference(treatment.expectancy, baseline.expectancy),
            sharpe=_difference(treatment.sharpe, baseline.sharpe),
            deflated_sharpe=_difference(treatment.deflated_sharpe, baseline.deflated_sharpe),
            max_drawdown=treatment.max_drawdown - baseline.max_drawdown,
            turnover=_difference(treatment.turnover, baseline.turnover),
            charges=treatment.charges - baseline.charges,
        )

    @staticmethod
    def _average_trade_delta(
        baseline: ArmMetrics, treatment: ArmMetrics, cost: JevCost
    ) -> Decimal | None:
        if baseline.average_trade is None or treatment.trade_count == 0:
            return None
        if treatment.average_trade is None:
            return None
        jev_per_trade = DecimalMath.divide(cost.inr, Decimal(treatment.trade_count))
        return treatment.average_trade - jev_per_trade - baseline.average_trade

    def _paired(
        self, base: MetricsReport, treat: MetricsReport, jev_cost_inr: Decimal
    ) -> PairedBootstrapReport | str:
        days = len(treat.daily_returns)
        if days == 0 or treat.starting_cash.amount <= ZERO:
            return "there are no trading days to pair"
        daily_cost = DecimalMath.divide(
            DecimalMath.divide(jev_cost_inr, treat.starting_cash.amount), Decimal(days)
        )
        net_of_jev = [r - daily_cost for r in treat.daily_returns]
        return self._bootstrap.compare(base.daily_returns, net_of_jev)

    @staticmethod
    def _regimes(base: MetricsReport, treat: MetricsReport) -> dict[str, RegimeDelta]:
        result: dict[str, RegimeDelta] = {}
        for regime in sorted(set(base.by_regime) | set(treat.by_regime)):
            before, after = base.by_regime.get(regime), treat.by_regime.get(regime)
            result[regime] = RegimeDelta(
                baseline_count=0 if before is None else before.count,
                treatment_count=0 if after is None else after.count,
                net_pnl_delta=_net(after) - _net(before),
                expectancy_delta=_difference(
                    None if after is None else after.expectancy,
                    None if before is None else before.expectancy,
                ),
            )
        return result


def _net(stats: TradeStatistics | None) -> Decimal:
    return ZERO if stats is None else stats.net_pnl.amount


def _difference(after: Decimal | None, before: Decimal | None) -> Decimal | None:
    return None if after is None or before is None else after - before
