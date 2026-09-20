"""`emporos backtest run` — a strategy over historical bars, read-only (plan.md §10).

Reads candles and instrument history from Mongo; needs no broker credentials and can place no
order. The JSON report is the same document the golden files hold; the text summary is printed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

import typer
import yaml
from pydantic import ValidationError

from emporos.backtest.document import BacktestDocument
from emporos.backtest.job import BacktestRequest
from emporos.backtest.settings import FillSettings
from emporos.backtest.summary import BacktestSummary
from emporos.cli.backtest_runtime import run_backtest
from emporos.cli.curation_commands import backtest_curate
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.money import Money

backtest_app = typer.Typer(help="Backtest a strategy over stored history.", no_args_is_help=True)
backtest_app.command("curate")(backtest_curate)

_DATE = ["%Y-%m-%d"]
_STRATEGY = typer.Argument(..., exists=True, dir_okay=False, help="A strategy YAML file.")
_FIRST = typer.Option(..., "--from", formats=_DATE, help="First trading day.")
_LAST = typer.Option(..., "--to", formats=_DATE, help="Last trading day (inclusive).")
_CASH = typer.Option("1000000", help="Starting cash, in rupees (a quoted number).")
_FILLS = typer.Option(None, help="YAML of fill-model settings.")
_REPORT = typer.Option(None, help="Write the full JSON report here.")
_UNIVERSE = typer.Option(
    False, help="Use an instrument's earliest recorded definition where the master has no history."
)
_FEES = typer.Option(False, help="Price days before the oldest fee schedule with that schedule.")


def _fills(path: Path | None) -> FillSettings:
    if path is None:
        return FillSettings()
    return FillSettings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _day(value: datetime) -> date:
    return value.date()


@backtest_app.command("run")
def backtest_run(
    strategy: Path = _STRATEGY,
    first: datetime = _FIRST,
    last: datetime = _LAST,
    cash: str = _CASH,
    fills: Path | None = _FILLS,
    report: Path | None = _REPORT,
    assume_current_universe: bool = _UNIVERSE,
    assume_earliest_fees: bool = _FEES,
) -> None:
    """Run STRATEGY over the trading days FROM..TO and print a full metrics report."""
    try:
        request = BacktestRequest(
            first_day=_day(first),
            last_day=_day(last),
            starting_cash=Money.of(cash),
            fills=_fills(fills),
            assume_current_universe=assume_current_universe,
        )
        result = asyncio.run(
            run_backtest(Settings.default(), strategy, request, assume_earliest_fees)
        )
    except (EmporosError, ValidationError, ValueError, InvalidOperation, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"backtest failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    document: dict[str, Any] = BacktestDocument().render(result)
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    typer.echo(BacktestSummary().render(document))
    if report is not None:
        typer.echo(f"\nfull report: {report}")
