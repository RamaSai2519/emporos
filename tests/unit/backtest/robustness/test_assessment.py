"""The assessor on the real engine: evidence comes from out-of-sample days only, and every piece
of it agrees with the run it was drawn from."""

from datetime import date
from decimal import Decimal

from emporos.backtest.robustness.assessment import HoldBaseline, RobustnessAssessor
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.backtest.robustness.perturbation import PerturbationRunner
from emporos.backtest.robustness.portfolio_economics import PortfolioCostModel
from emporos.backtest.robustness.trials import TrialStatistics
from emporos.backtest.tuning import NET_PNL
from emporos.backtest.walkforward_run import WalkForwardResult
from emporos.domain.experiments import Verdict
from emporos.domain.fees import FeeSchedule
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from tests.unit.backtest.test_walkforward_run import (
    CANDIDATES,
    RecordingBacktester,
    base_spec,
    runner,
    windows,
)

SCHEDULE = FeeSchedule(
    name="test",
    effective_from=date(2026, 1, 1),
    brokerage_flat=Money.of("20"),
    brokerage_percent=Decimal("0.03"),
    brokerage_minimum=Money.of("5"),
    stt_sell_percent=Decimal("0.025"),
    exchange_transaction_percent={Exchange.NSE: Decimal("0.0030699")},
    sebi_per_crore=Money.of("10"),
    stamp_duty_buy_percent=Decimal("0.003"),
    gst_percent=Decimal("18"),
)

D = Decimal


async def walk() -> WalkForwardResult:
    return await runner(RecordingBacktester(), objective=NET_PNL).run(
        base_spec(), CANDIDATES, windows()
    )


async def stats() -> TrialStatistics:
    return TrialStatistics(35, 0, None)


async def test_evidence_is_the_out_of_sample_trades_and_days() -> None:
    result = await walk()
    assessor = RobustnessAssessor(BenchmarkLoader().load(), stats)

    report = await assessor.assess("s", result, base_spec(), CANDIDATES)

    trades = [t for o in result.outcomes for t in o.test.trades]
    e = report.evidence
    assert e.trade_count == len(trades)
    assert e.net_pnl == sum((t.net_pnl.amount for t in trades), D(0))
    assert e.window_nets == tuple(o.test.metrics.trades.net_pnl.amount for o in result.outcomes)
    assert report.monte_carlo.trade_count == len(trades)
    assert report.deflated_sharpe.observations == sum(
        len(o.test.metrics.daily_returns) for o in result.outcomes
    )


async def test_pbo_is_computed_from_every_windows_training_scores() -> None:
    result = await walk()
    assessor = RobustnessAssessor(BenchmarkLoader().load(), stats)

    report = await assessor.assess("s", result, base_spec(), CANDIDATES)

    pbo = report.pbo.probability_of_overfitting
    assert report.pbo.computed and pbo is not None
    assert report.pbo.candidate_count == len(CANDIDATES)
    assert report.pbo.block_count == len(result.outcomes) - 1  # 5 windows, odd, one dropped
    assert Decimal(0) <= pbo <= Decimal(1)


async def test_direction_stats_split_the_same_trades_without_losing_one() -> None:
    result = await walk()
    report = await RobustnessAssessor(BenchmarkLoader().load(), stats).assess(
        "s", result, base_spec(), CANDIDATES
    )

    long_ = [t for o in result.outcomes for t in o.test.trades if t.direction.value == "LONG"]
    short = [t for o in result.outcomes for t in o.test.trades if t.direction.value == "SHORT"]
    direction = report.evidence.direction
    assert direction.long_count + direction.short_count == report.evidence.trade_count
    assert direction.long_count == len(long_) and direction.short_count == len(short)
    assert direction.long_net_pnl == sum((t.net_pnl.amount for t in long_), D(0))
    assert direction.short_net_pnl == sum((t.net_pnl.amount for t in short), D(0))
    assert direction.long_net_pnl + direction.short_net_pnl == report.evidence.net_pnl


async def test_without_a_perturbation_or_baseline_those_gates_are_unknown_not_passed() -> None:
    report = await RobustnessAssessor(BenchmarkLoader().load(), stats).assess(
        "s", await walk(), base_spec(), CANDIDATES
    )

    unknown = {g.name for g in report.verdict.gates if g.outcome.value == "unknown"}
    assert {"survives parameter changes", "beats the always-long baseline"} <= unknown
    assert report.perturbation is None and report.baseline_net_pnl is None
    assert report.verdict.verdict is not Verdict.VALIDATED


async def test_a_short_history_can_never_validate() -> None:
    report = await RobustnessAssessor(BenchmarkLoader().load(), stats).assess(
        "s", await walk(), base_spec(), CANDIDATES
    )

    history = next(g for g in report.verdict.gates if g.name == "enough history")
    assert history.outcome.value == "unknown"  # a fortnight of test days against 500 needed
    assert report.evidence.history_days < 500


async def test_perturbation_and_baseline_are_run_on_the_test_windows() -> None:
    result = await walk()
    backtester = RecordingBacktester()
    assessor = RobustnessAssessor(
        BenchmarkLoader().load(),
        stats,
        perturbation=PerturbationRunner(backtester),
        baseline=HoldBaseline(backtester, base_spec().config),
    )

    report = await assessor.assess("s", result, base_spec(), CANDIDATES)

    assert report.perturbation is not None and len(report.perturbation.runs) > 0
    assert report.baseline_net_pnl is not None
    test_windows = {o.window.test for o in result.outcomes}
    assert {w for w, _, _ in backtester.runs} <= test_windows  # never a training window


async def test_costs_include_every_declared_scenario_in_order() -> None:
    benchmark = BenchmarkLoader().load()

    report = await RobustnessAssessor(benchmark, stats).assess(
        "s", await walk(), base_spec(), CANDIDATES
    )

    assert [c.name for c in report.costs] == [s.name for s in benchmark.cost_scenarios]


async def test_edge_evidence_is_none_without_a_configured_portfolio_cost_model() -> None:
    assessor = RobustnessAssessor(BenchmarkLoader().load(), stats)

    report = await assessor.assess("s", await walk(), base_spec(), CANDIDATES)

    assert report.evidence.observed_edge_bps is None
    assert report.evidence.minimum_edge_bps is None


async def test_edge_evidence_is_computed_when_a_portfolio_cost_model_is_configured() -> None:
    cost_model = PortfolioCostModel(SCHEDULE, spread_bps=Decimal(2), slippage_bps=Decimal(5))
    assessor = RobustnessAssessor(
        BenchmarkLoader().load(), stats, portfolio_cost_model=cost_model
    )

    report = await assessor.assess("s", await walk(), base_spec(), CANDIDATES)

    assert report.evidence.observed_edge_bps is not None
    assert report.evidence.minimum_edge_bps is not None and report.evidence.minimum_edge_bps > 0
