"""`emporos worker run` — start the paper trading worker on live market data."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from emporos.broker.backoff import RandomJitter
from emporos.cli.graduation_composition import LIVE_RISK_TIER, live_graduation
from emporos.cli.history_runtime import instrument_master
from emporos.cli.live_feed import AngelOneFeedOpener
from emporos.cli.live_launch import (
    LiveLaunchCheck,
    LiveLaunchReport,
    MonitorSwitchView,
)
from emporos.cli.live_paper_worker import DEFAULT_PAPER_CASH, LivePaperWorker, PaperWorkerOptions
from emporos.cli.live_venue import AngelOneLiveVenueOpener
from emporos.cli.live_worker import LiveWorker, LiveWorkerOptions
from emporos.cli.strategy_composition import build_registry
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.money import Money
from emporos.instruments.cache import InstrumentCache
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.repositories import KillSwitchRepository
from emporos.persistence.verdict_store import MongoVerdictBook
from emporos.risk.kill_switch import FileSentinelKillSwitch, KillSwitchMonitor, MongoKillSwitch
from emporos.session.launch_gate import ConfigLaunchFacts, live_policy
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.resolution import StrategyConfigResolver

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
_CHECK = typer.Option(
    None, "--start", "-s", help="Strategy to check for live trading (repeatable)."
)
_ACKNOWLEDGE = typer.Option(
    None,
    "--acknowledge",
    help="NAME=STANDING (repeatable): run a strategy that is not validated, naming its standing "
    "(rejected, inconclusive, stale, none), as the dashboard asks you to type it.",
)
_ACCOUNT = typer.Option("paper", help="The paper account id (the API shows this account).")
_CASH = typer.Option(DEFAULT_PAPER_CASH, help="The paper account's starting cash, a quoted number.")
_PARITY = typer.Option(
    True,
    "--parity/--no-parity",
    help="After close-out, compare the day with the backtest of the same config (read-only).",
)
_LIVE_START = typer.Option(
    None, "--start", "-s", help="Strategy to take live (repeatable); every one must clear the gate."
)


async def _run_live_check(files: list[Path], start: list[str]) -> LiveLaunchReport:
    settings = Settings.default()
    clock, sleeper = SystemClock(), AsyncioSleeper()
    mongo = MongoClientFactory(settings)
    try:
        database = mongo.database()
        master = instrument_master(mongo, database)
        instruments = InstrumentCache()
        await instruments.load_from(master)
        loader = StrategyConfigLoader(StrategyConfigResolver(build_registry(), instruments))
        configs = [loader.load_file(path) for path in files]
        monitor = KillSwitchMonitor(
            [
                FileSentinelKillSwitch(settings.kill_switch_path),
                MongoKillSwitch(KillSwitchRepository(database)),
            ],
            clock,
            sleeper,
        )
        policy = live_policy(
            MongoVerdictBook(database), settings.live_trading_enabled, MonitorSwitchView(monitor),
            live_graduation(database, LIVE_RISK_TIER),
        )  # fmt: skip
        return await LiveLaunchCheck(policy, ConfigLaunchFacts(configs)).run(start)
    finally:
        await mongo.close()


@worker_app.command("run-live")
def worker_run_live(
    strategies: list[Path] | None = _FILES,
    start: list[str] | None = _CHECK,
) -> None:
    """Check whether a strategy may go live, and say why not. It never starts anything, whatever
    the answer — use `emporos worker live` to actually start one once every condition holds.

    Live trading needs LIVE_TRADING_ENABLED=true, the strategy `enabled: true` in its config, a
    VALIDATED verdict recorded for exactly that config, and the kill switch clear. This command
    logs in to nothing and places no order, by design, regardless of the outcome.
    """
    files = strategies or sorted(Path("config/strategies").glob("*.yaml"))
    try:
        report = asyncio.run(_run_live_check(files, list(start or [])))
    except (EmporosError, ValueError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"live check failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in report.lines():
        typer.echo(line)
    raise typer.Exit(code=report.exit_code)


@worker_app.command("run")
def worker_run(
    strategies: list[Path] | None = _FILES,
    start: list[str] | None = _START,
    acknowledge: list[str] | None = _ACKNOWLEDGE,
    account: str = _ACCOUNT,
    cash: str = _CASH,
    parity: bool = _PARITY,
) -> None:
    """Run one paper trading day: live ticks, real risk rules, simulated fills. Start before 09:15.

    With no --strategies every file in config/strategies is loadable; --start picks the ones to
    run immediately, and the dashboard's START command launches any other. Paper only: nothing
    here can place a real order.
    """
    files = strategies or sorted(Path("config/strategies").glob("*.yaml"))
    acknowledged = dict(item.split("=", 1) for item in acknowledge or [] if "=" in item)
    if len(acknowledged) != len(acknowledge or []):
        typer.secho("--acknowledge takes NAME=STANDING", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    options = PaperWorkerOptions(
        files, start or (), account, Money.of(cash), acknowledged=acknowledged,
        parity_report=parity,
    )  # fmt: skip
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


@worker_app.command("live")
def worker_live(
    strategies: list[Path] | None = _FILES,
    start: list[str] | None = _LIVE_START,
) -> None:
    """Run one live trading day, behind the full live gate (see `run-live`). Refused, this prints
    every reason and starts nothing — it logs in to Angel One only once every condition holds for
    every strategy named. There is no acknowledgement path; the operator's own flags and a
    validated verdict are the only way past it.
    """
    files = strategies or sorted(Path("config/strategies").glob("*.yaml"))
    settings, clock, sleeper = Settings.default(), SystemClock(), AsyncioSleeper()
    venues = AngelOneLiveVenueOpener(settings, clock, sleeper, RandomJitter())
    options = LiveWorkerOptions(files, list(start or ()), settings.angelone_client_code or "live")
    try:
        outcome = asyncio.run(LiveWorker(settings, options, venues, clock, sleeper).run())
    except (EmporosError, ValueError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"live worker failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    if isinstance(outcome, LiveLaunchReport):
        for line in outcome.lines():
            typer.echo(line)
        raise typer.Exit(code=1)
    typer.echo(
        f"session {outcome.final_state.value}" + (f": {outcome.failure}" if outcome.failure else "")
    )
    if outcome.failure:
        raise typer.Exit(code=2)
