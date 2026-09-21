"""`emporos worker run` — start the paper trading worker on live market data."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from emporos.broker.backoff import RandomJitter
from emporos.cli.live_feed import AngelOneFeedOpener
from emporos.cli.live_paper_worker import DEFAULT_PAPER_CASH, LivePaperWorker, PaperWorkerOptions
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.money import Money

worker_app = typer.Typer(help="The trading worker process.", no_args_is_help=True)

_FILES = typer.Option(
    None, "--strategies", "-f", exists=True, dir_okay=False, help="Strategy YAML (repeatable)."
)
_START = typer.Option(
    None,
    "--start",
    "-s",
    help="Strategy to run from the first bar (repeatable); others wait for START.",
)
_ACCOUNT = typer.Option("paper", help="The paper account id (the API shows this account).")
_CASH = typer.Option(DEFAULT_PAPER_CASH, help="The paper account's starting cash, a quoted number.")


@worker_app.command("run")
def worker_run(
    strategies: list[Path] | None = _FILES,
    start: list[str] | None = _START,
    account: str = _ACCOUNT,
    cash: str = _CASH,
) -> None:
    """Run one paper trading day: live ticks, real risk rules, simulated fills. Start before 09:15.

    With no --strategies every file in config/strategies is loadable; --start picks the ones to
    run immediately, and the dashboard's START command launches any other. Paper only: nothing
    here can place a real order.
    """
    files = strategies or sorted(Path("config/strategies").glob("*.yaml"))
    options = PaperWorkerOptions(files, start or (), account, Money.of(cash))
    settings, clock, sleeper = Settings.default(), SystemClock(), AsyncioSleeper()
    feeds = AngelOneFeedOpener(settings, clock, sleeper, RandomJitter())
    try:
        report = asyncio.run(LivePaperWorker(settings, options, feeds, clock, sleeper).run())
    except (EmporosError, ValueError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"worker failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"session {report.final_state.value}" + (f": {report.failure}" if report.failure else "")
    )
    if report.failure:
        raise typer.Exit(code=2)
