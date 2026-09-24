"""`emporos backtest curate` — run the pre-declared curation plan and write the honest report."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import typer
import yaml

from emporos.backtest.batch import Backtester
from emporos.backtest.cost_breakdown import CostBreakdownCalculator
from emporos.backtest.costs import EarliestBeforeFirst, ScheduleSource, StrictSchedules
from emporos.backtest.curation import (
    CurationRecord,
    CurationReport,
    SelectionCriteria,
    StrategyCurator,
)
from emporos.backtest.curation_run import CurationRun, PlannedStrategy, WindowPlan
from emporos.backtest.experiment_report import CurationExperimentReportBuilder
from emporos.backtest.parallel import default_workers
from emporos.backtest.risk_gate import RiskGateFactory
from emporos.backtest.robustness.assessment import HoldBaseline, RobustnessAssessor
from emporos.backtest.robustness.benchmark import (
    DEFAULT_BENCHMARK_FILE,
    BenchmarkConfig,
    BenchmarkLoader,
    BenchmarkScaler,
)
from emporos.backtest.robustness.holdout import FinalHoldoutReservation
from emporos.backtest.robustness.perturbation import PerturbationRunner
from emporos.backtest.robustness.portfolio_economics import PortfolioCostModel
from emporos.backtest.robustness.recording import (
    LedgerRecorder,
    ResultRecorder,
    TrialContext,
    WalkForwardTrials,
)
from emporos.backtest.robustness.report import RobustnessDocument
from emporos.backtest.robustness.trials import InMemoryTrialLedger, TrialLedger
from emporos.backtest.tuning import SHARPE, ParameterCandidate
from emporos.cli.backtest_parallel import CurationRecipe, curation_batch
from emporos.cli.backtest_runtime import candle_cache_root, open_backtest_runtime
from emporos.cli.experiment_declarations import DeclarationGate, ExperimentDeclarationLoader
from emporos.cli.experiment_provenance import CurationVersionStamp, GitRepository
from emporos.cli.experiment_registry import (
    DEFAULT_EXPERIMENTS_DIR,
    ExperimentPublication,
    FileExperimentRegistry,
)
from emporos.cli.program_trials import ProgramTrialCountFactory
from emporos.cli.strategy_composition import build_registry
from emporos.cli.verdict_commands import record_curation
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.core.ids import IdGenerator
from emporos.domain.fees import FeeSchedule
from emporos.domain.instruments import InstrumentResolver
from emporos.domain.money import Money
from emporos.domain.research_experiments import (
    CostBreakdown,
    ExperimentDeclaration,
    VersionStamp,
)
from emporos.domain.sizing import DeclaredSize, SizeResolver
from emporos.persistence.trial_ledger import MongoTrialLedger
from emporos.portfolio.fee_schedules import FeeScheduleError, FeeScheduleLibrary
from emporos.risk.config import RiskLimitsLoader
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver

_PLAN = typer.Option(Path("config/curation/plan.yaml"), exists=True, dir_okay=False)
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"])
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"])
_CASH = typer.Option(None, help="Starting cash, a quoted number (default: the benchmark capital).")
_BENCHMARK = typer.Option(
    DEFAULT_BENCHMARK_FILE,
    exists=True,
    dir_okay=False,
    help="Benchmark capital, costs and verdict thresholds.",
)
_BASELINE = "hold_baseline_v1"
_REPORT = typer.Option(Path("docs/strategies/curation.md"), help="The human-readable report.")
_FEES = typer.Option(False, help="Price days before the oldest fee schedule with it.")
_ONLY = typer.Option(None, "--only", help="Run just this strategy from the plan (repeatable).")
_JSON = typer.Option(
    Path("docs/strategies/curation.json"), "--json", help="The machine-readable record."
)
_RECORD = typer.Option(
    True, "--record/--no-record", help="Append every backtest tried to the trial ledger."
)
_WORKERS = typer.Option(
    None,
    min=1,
    help="Backtests run at once, in separate processes (default: one fewer than the CPU cores). "
    "1 runs them one after another in this process.",
)
_EXPERIMENT = typer.Option(None, help="Ledger experiment name (default: curation-<today>).")
_DECLARATION = typer.Option(
    None,
    exists=True,
    dir_okay=False,
    help="Experiment declaration (config/experiments/<slug>.yaml), committed BEFORE the run: on "
    "completion the result is published as an experiment report and the index rebuilt. "
    "Describes one experiment, so pick one strategy with --only.",
)
_EXPERIMENTS_DIR = typer.Option(
    DEFAULT_EXPERIMENTS_DIR, help="Where published experiment reports live."
)
_POSITION_VALUE = typer.Option(
    None,
    help="Rupees per position to judge at, a quoted number. Default: the declaration's "
    "position_value, else config/risk.yaml's max_position_value. A declaration that states one "
    "fixes it: a different value here is refused.",
)
_UNCOMMITTED = typer.Option(
    False,
    "--allow-uncommitted-declaration",
    help="Publish even though the declaration is not committed (it then proves nothing about "
    "having been declared before the run).",
)


def _record_document(record: CurationRecord) -> dict[str, Any]:
    stats = record.pooled.statistics
    return {
        "strategy": record.strategy,
        "passed": record.verdict.passed,
        "candidates": list(record.candidates),
        "out_of_sample": {
            "trades": stats.count,
            "net_pnl": str(stats.net_pnl.amount),
            "gross_pnl": str(stats.gross_pnl.amount),
            "charges": str(stats.fees.amount),
            "win_rate": None if stats.win_rate is None else str(stats.win_rate),
            "profit_factor": None if stats.profit_factor is None else str(stats.profit_factor),
            "net_at_double_costs": str(record.pooled.net_at_double_costs),
            "window_nets": [str(n) for n in record.pooled.window_nets],
            "window_max_drawdowns": [str(d) for d in record.pooled.window_drawdowns],
            "instrument_nets": {k: str(v) for k, v in sorted(record.pooled.symbol_nets.items())},
            "compounded_return": str(record.compounded_return),
            "expectancy": None if stats.expectancy is None else str(stats.expectancy),
            "sharpe": _sharpe(record),
            "max_window_drawdown": str(max(record.pooled.window_drawdowns, default=Decimal(0))),
        },
        "dataset": _dataset(record),
        "behaviour_hashes": dict(sorted(record.behaviour_hashes.items())),
        "costs": _costs(record.costs),
        "windows": [list(w) for w in record.windows],
        "checks": [
            {"name": c.name, "passed": c.passed, "actual": c.actual, "required": c.required}
            for c in record.verdict.checks
        ],
        "robustness": None
        if record.robustness is None
        else RobustnessDocument().of(record.robustness),
    }


def _sharpe(record: CurationRecord) -> str | None:
    """The pooled out-of-sample annualised Sharpe, already computed by the robustness assessment
    from every window's daily returns; absent when no assessment was run."""
    if record.robustness is None or record.robustness.deflated_sharpe.annualised_sharpe is None:
        return None
    return str(record.robustness.deflated_sharpe.annualised_sharpe)


def _dataset(record: CurationRecord) -> dict[str, Any] | None:
    p = record.provenance
    if p is None:
        return None
    return {
        "timeframe": p.dataset_timeframe.value,
        "first": p.dataset_first.isoformat(),
        "last": p.dataset_last.isoformat(),
        "universe_hash": p.universe_hash,
        "calendar_version": p.calendar_version,
        "quarantine_hash": p.quarantine_hash,
    }


def _costs(costs: CostBreakdown | None) -> dict[str, str | None] | None:
    if costs is None:
        return None
    return {
        "brokerage": str(costs.brokerage),
        "statutory": str(costs.statutory),
        "spread": str(costs.spread),
        "slippage": str(costs.slippage),
        "total": str(costs.total),
        "per_trade_bps": None if costs.per_trade_bps is None else str(costs.per_trade_bps),
        "per_trade_inr": None if costs.per_trade_inr is None else str(costs.per_trade_inr),
    }


class PlanHoldout:
    """Reads the plan's pre-declared `holdout_days`. Absent is legal, and means nothing is
    reserved: the run still happens, but its report can never be ACCEPTED."""

    @staticmethod
    def reservation(plan: dict[str, Any]) -> FinalHoldoutReservation | None:
        days = plan.get("holdout_days")
        if days is None:
            return None
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise ValueError("the plan's holdout_days must be a positive whole number of days")
        return FinalHoldoutReservation(timedelta(days=days))


class PlanCostModel:
    """The one cost model a curation run's breakdown and cost-error gate both read: the fee
    schedule in force on the last day (the oldest one when the run predates them all) with the
    benchmark's spread and slippage."""

    @staticmethod
    def schedule(library: FeeScheduleLibrary, last_day: date) -> FeeSchedule:
        try:
            return library.for_date(last_day)
        except FeeScheduleError:
            return library.earliest

    @staticmethod
    def of(
        library: FeeScheduleLibrary, benchmark: BenchmarkConfig, last_day: date
    ) -> PortfolioCostModel:
        schedule = PlanCostModel.schedule(library, last_day)
        return PortfolioCostModel(schedule, benchmark.spread_bps, benchmark.slippage_bps)


@dataclass(frozen=True)
class CurationOutcome:
    records: list[CurationRecord]
    criteria: SelectionCriteria
    versions: VersionStamp  # what every strategy of this run shares: cost model and code


async def _curate(
    plan_path: Path,
    benchmark_path: Path,
    first: datetime,
    last: datetime,
    cash: str | None,
    assume_fees: bool,
    only: list[str] | None,
    record_trials: bool,
    experiment: str | None,
    workers: int,
    size: DeclaredSize,
    single_experiment: bool = False,
) -> CurationOutcome:
    plan: dict[str, Any] = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    if plan.get("objective") != "sharpe":
        raise ValueError("the plan's objective must be sharpe (the only one pre-registered)")
    known = {e["name"] for e in plan["strategies"]}
    unknown = [name for name in only or () if name not in known]
    if unknown:
        raise ValueError(
            f"--only names not in {plan_path}: {', '.join(unknown)} "
            f"(available: {', '.join(sorted(known))})"
        )
    selected = [e for e in plan["strategies"] if not only or e["name"] in only]
    if single_experiment and len(selected) != 1:
        raise ValueError(
            f"a declaration describes one experiment, but {len(selected)} strategies would run: "
            "pick one with --only"
        )
    windows = plan["windows"]
    holdout = PlanHoldout.reservation(plan)
    if holdout is None:
        typer.secho(
            f"{plan_path} reserves no holdout (holdout_days): the run cannot be ACCEPTED",
            fg=typer.colors.YELLOW,
        )
    benchmark = BenchmarkLoader(benchmark_path).load()
    scaler = BenchmarkScaler(benchmark, size.position_value)
    registry = build_registry()
    library = FeeScheduleLibrary.from_directory()

    def load(file: str | Path, resolver: InstrumentResolver) -> ResolvedStrategyConfig:
        loader = StrategyConfigLoader(StrategyConfigResolver(registry, resolver))
        return scaler.strategy(loader.load_file(Path(file)))

    def strategy(entry: dict[str, Any]) -> PlannedStrategy:
        return PlannedStrategy(
            entry["name"],
            lambda resolver: load(entry["file"], resolver),
            tuple(ParameterCandidate(c["name"], c["overrides"]) for c in entry["candidates"]),
        )

    def schedules() -> ScheduleSource:
        if assume_fees:
            return EarliestBeforeFirst(library)
        return StrictSchedules(library)

    criteria = SelectionCriteria()
    cost_model = PlanCostModel.of(library, benchmark, last.date())
    settings = Settings.default()
    async with open_backtest_runtime(settings) as runtime:
        ledger: TrialLedger = InMemoryTrialLedger()
        if record_trials:
            ledger = MongoTrialLedger(runtime.database)
        trials = ProgramTrialCountFactory().build(
            runtime.database, None if record_trials else ledger
        )

        window_plan = WindowPlan(
            first.date(),
            last.date(),
            timedelta(days=windows["train_days"]),
            timedelta(days=windows["test_days"]),
            timedelta(days=windows["embargo_days"]),
        )
        limits = scaler.limits(RiskLimitsLoader().load())
        cache_root = candle_cache_root(settings)
        if runtime.cache is None:
            raise ValueError("the backtest runtime has no candle cache")

        def recipe(snapshot: Path) -> CurationRecipe:
            return CurationRecipe(
                cache_root, snapshot, runtime.eras, window_plan.bounds()[0], True, assume_fees,
                limits,
            )  # fmt: skip

        with curation_batch(workers, runtime.cache, cache_root, recipe) as batch:

            def assessor(
                backtester: Backtester, resolver: InstrumentResolver
            ) -> RobustnessAssessor:
                baseline = HoldBaseline(
                    backtester, load(f"config/strategies/{_BASELINE}.yaml", resolver), batch
                )
                return RobustnessAssessor(
                    benchmark, trials, PerturbationRunner(backtester, batch=batch),
                    baseline, portfolio_cost_model=cost_model,
                )  # fmt: skip

            run = CurationRun(
                runtime.reader, registry, runtime.instruments,
                schedules,
                RiskGateFactory(limits), StrategyCurator(criteria),
                SHARPE, recorder=_recorder(
                    ledger, experiment, first, last, assume_fees,
                    runtime.cache.fingerprint if runtime.cache else None,
                ),
                assessors=assessor, batch=batch,
                calendar=runtime.calendar, quarantine=runtime.quarantine,
                holdout=holdout, costs=CostBreakdownCalculator(cost_model),
            )  # fmt: skip
            records = await run.run(
                [strategy(e) for e in selected],
                window_plan,
                Money.of(cash or str(benchmark.capital)),
                progress=lambda message: typer.echo(message),
            )
            if record_trials:  # a run kept out of the ledger is exploratory, and no verdict either
                recorded = await record_curation(
                    runtime.database,
                    records,
                    {e["name"]: Path(e["file"]) for e in plan["strategies"]},
                    benchmark_path,
                    first.date().isoformat(),
                    last.date().isoformat(),
                    experiment or f"curation-{datetime.now(UTC):%Y-%m-%d}",
                    position_value=size.position_value,
                )
                typer.echo(f"{len(recorded)} verdict(s) recorded")
    versions = CurationVersionStamp(GitRepository()).of(
        benchmark_path, benchmark, PlanCostModel.schedule(library, last.date())
    )
    return CurationOutcome(records, criteria, versions)


def _recorder(
    ledger: TrialLedger,
    experiment: str | None,
    first: datetime,
    last: datetime,
    assume_fees: bool,
    dataset_stamp: Callable[[], str] | None = None,
) -> ResultRecorder:
    now = datetime.now(UTC)
    fees = "earliest fee schedule assumed before the first" if assume_fees else "dated fees, strict"
    context = TrialContext(
        experiment=experiment or f"curation-{now:%Y-%m-%d}",
        batch=IdGenerator().new_ulid(),
        dataset_version=f"candles 5m {first:%Y-%m-%d}..{last:%Y-%m-%d}",
        cost_model=f"Angel One {fees}; platform risk gate at the benchmark capital",
        recorded_at=now,
    )
    return LedgerRecorder(ledger, WalkForwardTrials(context, dataset_stamp=dataset_stamp))


def backtest_curate(
    plan: Path = _PLAN, first: datetime = _FROM, last: datetime = _TO, cash: str | None = _CASH,
    benchmark: Path = _BENCHMARK,
    report: Path = _REPORT, record: Path = _JSON,
    assume_earliest_fees: bool = _FEES,
    only: list[str] | None = _ONLY,
    record_trials: bool = _RECORD,
    experiment: str | None = _EXPERIMENT,
    workers: int | None = _WORKERS,
    declaration: Path | None = _DECLARATION,
    experiments_dir: Path = _EXPERIMENTS_DIR,
    allow_uncommitted_declaration: bool = _UNCOMMITTED,
    position_value: str | None = _POSITION_VALUE,
) -> None:  # fmt: skip
    """Walk-forward every strategy in PLAN at the benchmark capital; classify each, failures too."""
    try:
        declared = _declared(declaration, allow_uncommitted_declaration)
        risk_limit = RiskLimitsLoader().load().max_position_value
        size = _size_of(declared, position_value, risk_limit)
        typer.echo(f"judged at {size.position_value} rupees per position ({size.source.value})")
        outcome = asyncio.run(
            _curate(
                plan,
                benchmark,
                first,
                last,
                cash,
                assume_earliest_fees,
                only,
                record_trials,
                experiment,
                workers or default_workers(),
                size,
                single_experiment=declared is not None,
            )
        )
    except (EmporosError, ValueError, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"curation failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    records = outcome.records
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(CurationReport().render(records, outcome.criteria) + "\n", encoding="utf-8")
    record.write_text(
        json.dumps([_record_document(r) for r in records], indent=2) + "\n", encoding="utf-8"
    )
    for r in records:
        verdict = (
            "" if r.robustness is None else f" -> {r.robustness.verdict.verdict.value.upper()}"
        )
        typer.echo(f"{r.strategy}: {'PASSED' if r.verdict.passed else 'FAILED'}{verdict}")
    typer.echo(f"report: {report}\nrecord: {record}")
    if declared is not None:
        _publish(records[0], declared, outcome.versions, experiments_dir, size, risk_limit)


def _size_of(
    declared: ExperimentDeclaration | None, override: str | None, risk_limit: Decimal
) -> DeclaredSize:
    """The size every window, perturbation and baseline of this run is judged at: what the
    declaration fixed, or the command line, or the platform's own limit. Never chosen afterwards."""
    try:
        stated = None if override is None else Decimal(override)
    except InvalidOperation:
        raise ValueError(f"--position-value is not a number: {override!r}") from None
    return SizeResolver(risk_limit).resolve(
        declared=None if declared is None else declared.position_value, override=stated
    )


def _declared(declaration: Path | None, allow_uncommitted: bool) -> ExperimentDeclaration | None:
    if declaration is None:
        return None
    gate = DeclarationGate(ExperimentDeclarationLoader(), GitRepository())
    return gate.load(declaration, allow_uncommitted=allow_uncommitted)


def _publish(
    record: CurationRecord,
    declaration: ExperimentDeclaration,
    versions: VersionStamp,
    experiments_dir: Path,
    size: DeclaredSize,
    risk_limit: Decimal,
) -> None:
    publication = ExperimentPublication(
        CurationExperimentReportBuilder(size, risk_limit), FileExperimentRegistry(experiments_dir)
    )
    try:
        report, outcome = publication.publish(record, declaration, versions)
    except EmporosError as error:
        typer.secho(f"experiment not published: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"experiment {report.experiment_id}: {report.outcome.value.upper()} ({outcome.value}) "
        f"in {experiments_dir}"
    )
