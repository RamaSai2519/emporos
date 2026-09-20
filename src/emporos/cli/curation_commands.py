"""`emporos backtest curate` — run the pre-declared curation plan and write the honest report."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import typer
import yaml

from emporos.backtest.costs import EarliestBeforeFirst, ScheduleSource, StrictSchedules
from emporos.backtest.curation import (
    CurationRecord,
    CurationReport,
    SelectionCriteria,
    StrategyCurator,
)
from emporos.backtest.curation_run import CurationRun, PlannedStrategy, WindowPlan
from emporos.backtest.risk_gate import RiskGateFactory
from emporos.backtest.tuning import SHARPE, ParameterCandidate
from emporos.cli.backtest_runtime import open_backtest_runtime
from emporos.cli.strategy_composition import build_registry
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.instruments import InstrumentResolver
from emporos.domain.money import Money
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.risk.config import RiskLimitsLoader
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.resolution import StrategyConfigResolver

_PLAN = typer.Option(Path("config/curation/plan.yaml"), exists=True, dir_okay=False)
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"])
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"])
_CASH = typer.Option("100000", help="Starting cash, a quoted number.")
_REPORT = typer.Option(Path("docs/strategies/curation.md"), help="The human-readable report.")
_FEES = typer.Option(False, help="Price days before the oldest fee schedule with it.")
_JSON = typer.Option(Path("docs/strategies/curation.json"), help="The machine-readable record.")


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
    }


async def _curate(
    plan_path: Path, first: datetime, last: datetime, cash: str, assume_fees: bool
) -> tuple[list[CurationRecord], SelectionCriteria]:
    plan: dict[str, Any] = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    if plan.get("objective") != "sharpe":
        raise ValueError("the plan's objective must be sharpe (the only one pre-registered)")
    windows = plan["windows"]
    registry = build_registry()
    library = FeeScheduleLibrary.from_directory()

    def strategy(entry: dict[str, Any]) -> PlannedStrategy:
        def config_for(resolver: InstrumentResolver):  # type: ignore[no-untyped-def]
            loader = StrategyConfigLoader(StrategyConfigResolver(registry, resolver))
            return loader.load_file(Path(entry["file"]))

        return PlannedStrategy(
            entry["name"],
            config_for,
            tuple(ParameterCandidate(c["name"], c["overrides"]) for c in entry["candidates"]),
        )

    def schedules() -> ScheduleSource:
        if assume_fees:
            return EarliestBeforeFirst(library)
        return StrictSchedules(library)

    criteria = SelectionCriteria()
    async with open_backtest_runtime(Settings.default()) as runtime:
        run = CurationRun(
            runtime.reader, registry, runtime.instruments,
            schedules,
            RiskGateFactory(RiskLimitsLoader().load()), StrategyCurator(criteria), SHARPE,
        )  # fmt: skip
        records = await run.run(
            [strategy(e) for e in plan["strategies"]],
            WindowPlan(
                first.date(),
                last.date(),
                timedelta(days=windows["train_days"]),
                timedelta(days=windows["test_days"]),
                timedelta(days=windows["embargo_days"]),
            ),
            Money.of(cash),
            progress=lambda message: typer.echo(message),
        )
    return records, criteria


def backtest_curate(
    plan: Path = _PLAN, first: datetime = _FROM, last: datetime = _TO, cash: str = _CASH,
    report: Path = _REPORT, record: Path = _JSON,
    assume_earliest_fees: bool = _FEES,
) -> None:  # fmt: skip
    """Walk-forward every strategy in PLAN under the risk rules; report passes AND failures."""
    try:
        records, criteria = asyncio.run(_curate(plan, first, last, cash, assume_earliest_fees))
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
        typer.echo(f"{r.strategy}: {'PASSED' if r.verdict.passed else 'FAILED'}")
    typer.echo(f"report: {report}\nrecord: {record}")
