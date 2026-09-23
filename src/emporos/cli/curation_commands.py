"""`emporos backtest curate` — run the pre-declared curation plan and write the honest report."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer
import yaml

from emporos.backtest.batch import Backtester
from emporos.backtest.costs import EarliestBeforeFirst, ScheduleSource, StrictSchedules
from emporos.backtest.curation import (
    CurationRecord,
    CurationReport,
    SelectionCriteria,
    StrategyCurator,
)
from emporos.backtest.curation_run import CurationRun, PlannedStrategy, WindowPlan
from emporos.backtest.parallel import default_workers
from emporos.backtest.risk_gate import RiskGateFactory
from emporos.backtest.robustness.assessment import HoldBaseline, RobustnessAssessor
from emporos.backtest.robustness.benchmark import (
    DEFAULT_BENCHMARK_FILE,
    BenchmarkLoader,
    BenchmarkScaler,
)
from emporos.backtest.robustness.perturbation import PerturbationRunner
from emporos.backtest.robustness.recording import (
    LedgerRecorder,
    ResultRecorder,
    TrialContext,
    WalkForwardTrials,
)
from emporos.backtest.robustness.report import RobustnessDocument
from emporos.backtest.robustness.trials import InMemoryTrialLedger, TrialLedger, TrialStatistics
from emporos.backtest.tuning import SHARPE, ParameterCandidate
from emporos.cli.backtest_parallel import CurationRecipe, curation_batch
from emporos.cli.backtest_runtime import candle_cache_root, open_backtest_runtime
from emporos.cli.strategy_composition import build_registry
from emporos.cli.verdict_commands import record_curation
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.core.ids import IdGenerator
from emporos.domain.instruments import InstrumentResolver
from emporos.domain.money import Money
from emporos.persistence.trial_ledger import MongoTrialLedger
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
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
        },
        "windows": [list(w) for w in record.windows],
        "checks": [
            {"name": c.name, "passed": c.passed, "actual": c.actual, "required": c.required}
            for c in record.verdict.checks
        ],
        "robustness": None
        if record.robustness is None
        else RobustnessDocument().of(record.robustness),
    }


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
) -> tuple[list[CurationRecord], SelectionCriteria]:
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
    windows = plan["windows"]
    benchmark = BenchmarkLoader(benchmark_path).load()
    scaler = BenchmarkScaler(benchmark)
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
    settings = Settings.default()
    async with open_backtest_runtime(settings) as runtime:
        ledger: TrialLedger = InMemoryTrialLedger()
        if record_trials:
            ledger = MongoTrialLedger(runtime.database)

        async def trial_statistics() -> TrialStatistics:
            return TrialStatistics.of(await ledger.all())

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
                    benchmark, trial_statistics, PerturbationRunner(backtester, batch=batch),
                    baseline,
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
            )  # fmt: skip
            records = await run.run(
                [strategy(e) for e in plan["strategies"] if not only or e["name"] in only],
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
                )
                typer.echo(f"{len(recorded)} verdict(s) recorded")
    return records, criteria


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
) -> None:  # fmt: skip
    """Walk-forward every strategy in PLAN at the benchmark capital; classify each, failures too."""
    try:
        records, criteria = asyncio.run(
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
            )
        )
    except (EmporosError, ValueError, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"curation failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(CurationReport().render(records, criteria) + "\n", encoding="utf-8")
    record.write_text(
        json.dumps([_record_document(r) for r in records], indent=2) + "\n", encoding="utf-8"
    )
    for r in records:
        verdict = (
            "" if r.robustness is None else f" -> {r.robustness.verdict.verdict.value.upper()}"
        )
        typer.echo(f"{r.strategy}: {'PASSED' if r.verdict.passed else 'FAILED'}{verdict}")
    typer.echo(f"report: {report}\nrecord: {record}")
