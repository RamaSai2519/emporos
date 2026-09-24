"""Hand-built Jev on/off comparisons with known numbers, so the incremental analysis and the
verdict policy can be tested without running a backtest."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from emporos.backtest.costs import CostSummary
from emporos.backtest.feed import FeedWindow
from emporos.backtest.fingerprint import ExperimentFingerprinter
from emporos.backtest.flow import RunCounters
from emporos.backtest.jev_pnl import JevPnlComparison
from emporos.backtest.metrics.report import MetricsCalculator, MetricsReport
from emporos.backtest.metrics.trades import TradeAnalyzer
from emporos.backtest.multi_engine import (
    MultiStrategyBacktestResult,
    MultiStrategyBacktestSpec,
    StrategyIdentity,
)
from emporos.backtest.portfolio import ClosedTrade, EquityPoint
from emporos.domain.money import Money
from emporos.opportunity.allocator import AllocationConstraints
from emporos.opportunity.jev_experiment import JevRunSummary
from tests.support.backtest_metrics import trade
from tests.support.strategies import INSTRUMENT, make_config

START = "100000"
_DAY0 = datetime(2026, 1, 5, 6, 0, tzinfo=UTC)
_CONSTRAINTS = AllocationConstraints(
    production_capital=Money.of("5000"),
    max_simultaneous_positions=5,
    max_risk_per_trade=Money.of("500"),
    max_portfolio_risk=Money.of("2000"),
)


def spec() -> MultiStrategyBacktestSpec:
    return MultiStrategyBacktestSpec(
        configs=[make_config(name="momentum_v1", instruments=(INSTRUMENT,))],
        window=FeedWindow(_DAY0, _DAY0 + timedelta(days=60)),
        starting_cash=Money.of(START),
        constraints=_CONSTRAINTS,
    )


def metrics(
    equities: list[str],
    trades: list[ClosedTrade] | None = None,
    by_regime: dict[str, list[ClosedTrade]] | None = None,
) -> MetricsReport:
    """Metrics for a run whose end-of-day equity is `equities`, one point per calendar day."""
    curve = [
        EquityPoint(_DAY0 + timedelta(days=n), Money.of(e), Money.zero(), 0)
        for n, e in enumerate(equities)
    ]
    report = MetricsCalculator().calculate(Money.of(START), curve, trades or [], Money.of("300000"))
    if by_regime is None:
        return report
    grouped = {name: TradeAnalyzer().analyze(group) for name, group in by_regime.items()}
    return replace(report, by_regime=grouped)


def result(report: MetricsReport, jev: JevRunSummary | None = None) -> MultiStrategyBacktestResult:
    return MultiStrategyBacktestResult(
        strategies=(StrategyIdentity("run-1", "momentum_v1", "sha256:cfg"),),
        spec=spec(),
        alerts=(),
        counters=RunCounters(),
        costs=CostSummary((), 0, True),
        risk_gate="pass-through",
        trades=(),
        metrics=report,
        open_positions_at_end=0,
        jev=jev or JevRunSummary(),
    )


def comparison(
    baseline: MetricsReport, treatment: MetricsReport, jev: JevRunSummary | None = None
) -> JevPnlComparison:
    return JevPnlComparison(
        baseline=result(baseline),
        treatment=result(treatment, jev),
        fingerprint=ExperimentFingerprinter().fingerprint(spec()),
    )


def winning_trades(count: int, net: str = "100") -> list[ClosedTrade]:
    return [trade(net, minute * 3) for minute in range(count)]
