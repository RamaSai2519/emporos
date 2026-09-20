"""The `emporos` CLI.

The Next.js dashboard is the primary operator interface (Decision 12) — this
CLI exists for local development, CI, and break-glass operations when the
dashboard is unreachable. Every command below is a placeholder wired up in
Phase 1 (EM-9); each is implemented by the phase noted in its docstring.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx
import typer

from emporos.cli.api_commands import api_app
from emporos.cli.backtest_commands import backtest_app
from emporos.cli.history_composition import resolve_symbols
from emporos.cli.history_runtime import open_bar_fetch_runtime, open_history_runtime
from emporos.cli.kill_switch_commands import halt, kill_switch_status, resume
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import IST, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError, EmporosError
from emporos.domain.candles import Timeframe
from emporos.domain.instruments import Exchange
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.differ import InstrumentDiffer
from emporos.instruments.downloader import MASTER_URL, InstrumentMasterDownloader
from emporos.instruments.store import MongoInstrumentMasterStore
from emporos.instruments.sync import InstrumentSyncService, SyncOutcome, SyncResult
from emporos.instruments.validator import InstrumentMasterValidator
from emporos.marketdata.session import SessionWindow
from emporos.marketdata.timeframes import DERIVED_TIMEFRAMES
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationReport, MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.records import InstrumentRecord
from emporos.persistence.repositories import InstrumentRepository, InstrumentVersionRepository
from emporos.persistence.schema import PLATFORM_SCHEMA
from emporos.persistence.staged_load import StagedCollectionLoader
from emporos.persistence.transactions import TransactionRunner

_DOWNLOAD_TIMEOUT_SECONDS = 120.0  # the upstream file is ~35 MB

app = typer.Typer(
    name="emporos",
    help="Emergency and CI operator commands for the Emporos trading platform.",
    no_args_is_help=True,
)

db_app = typer.Typer(help="Database migrations and maintenance.", no_args_is_help=True)
instruments_app = typer.Typer(help="Instrument master sync.", no_args_is_help=True)
app.add_typer(db_app, name="db")
kill_switch_app = typer.Typer(help="Inspect the kill switch.", no_args_is_help=True)
kill_switch_app.command("status")(kill_switch_status)
history_app = typer.Typer(
    help="Historical candles: backfill, gap repair, calendar.", no_args_is_help=True
)
app.add_typer(instruments_app, name="instruments")
app.add_typer(history_app, name="history")
app.add_typer(backtest_app, name="backtest")
app.add_typer(api_app, name="api")
app.command("halt")(halt)
app.command("resume")(resume)
app.add_typer(kill_switch_app, name="kill-switch")


def _not_implemented(feature: str, jira_ref: str) -> None:
    typer.secho(
        f"'{feature}' is not implemented yet — see {jira_ref}.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=1)


@app.command()
def run() -> None:
    """Start the trading worker (market data, risk, execution, session lifecycle)."""
    _not_implemented("run", "EM-31 onward")


@app.command()
def backfill() -> None:
    """Backfill historical candles for the instrument universe."""
    _not_implemented("backfill", "EM-55")


async def _migrate() -> MigrationReport:
    factory = MongoClientFactory(Settings.default())
    try:
        runner = MigrationRunner(MongoSchemaStore(factory.database()), PLATFORM_SCHEMA)
        return await runner.apply()
    finally:
        await factory.close()


@db_app.command("migrate")
def db_migrate() -> None:
    """Create every collection and index (idempotent — safe to re-run)."""
    try:
        report = asyncio.run(_migrate())
    except EmporosError as error:
        typer.secho(f"migration failed: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(report.summary())


_SYNC_EXIT_CODES = {
    SyncOutcome.APPLIED: 0,
    SyncOutcome.NO_CHANGE: 0,
    SyncOutcome.REJECTED: 1,
    SyncOutcome.UNAVAILABLE: 2,
}


async def _sync_instruments() -> SyncResult:
    settings = Settings.default()
    factory = MongoClientFactory(settings)
    try:
        database = factory.database()
        store = MongoInstrumentMasterStore(
            TransactionRunner(factory.client),
            InstrumentRepository(database),
            InstrumentVersionRepository(database),
            StagedCollectionLoader(
                database, PLATFORM_SCHEMA.spec_for(Collection.INSTRUMENTS), InstrumentRecord
            ),
        )
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SECONDS) as client:
            service = InstrumentSyncService(
                source=InstrumentMasterDownloader(
                    client, settings.instrument_master_url or MASTER_URL
                ),
                validator=InstrumentMasterValidator(),
                differ=InstrumentDiffer(),
                store=store,
                cache=InstrumentCache(),
                alerts=LogAlertSink(),
                clock=SystemClock(),
            )
            return await service.run()
    finally:
        await factory.close()


@instruments_app.command("sync")
def instruments_sync() -> None:
    """Download and apply the latest instrument master.

    Exit code 0: applied or nothing to do. 1: rejected by validation (yesterday's data kept).
    2: upstream unavailable (yesterday's data kept).
    """
    try:
        result = asyncio.run(_sync_instruments())
    except EmporosError as error:
        typer.secho(f"instrument sync failed: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    colour = typer.colors.GREEN if result.succeeded else typer.colors.RED
    typer.secho(f"instruments sync {result.outcome.value}: {result.message}", fg=colour)
    raise typer.Exit(code=_SYNC_EXIT_CODES[result.outcome])


_SYMBOLS = typer.Option(..., "--symbol", "-s", help="Trading symbol, e.g. SBIN-EQ (repeatable).")
_DAYS = typer.Option(30, "--days", "-d", min=1, help="How many calendar days back to cover.")
_OPTIONAL_SYMBOLS = typer.Option([], "--symbol", "-s", help="Limit to these symbols.")


def _window(days: int) -> tuple[date, date]:
    today = datetime.now(IST).date()
    return today - timedelta(days=days), today


@dataclass(frozen=True)
class HistoryOutcome:
    summary: str
    complete: bool


async def _backfill(symbols: list[str], days: int, reconcile: bool) -> HistoryOutcome:
    async with open_history_runtime(Settings.default()) as runtime:
        instruments = resolve_symbols(runtime.instruments, Exchange.NSE, symbols)
        first, last = _window(days)
        if reconcile:
            fixed = await runtime.stack.reconciler.reconcile(instruments, first, last)
            failed = sum(len(r.failed_days) for r in fixed.values())
            written = sum(r.candles_written for r in fixed.values())
            return HistoryOutcome(
                f"reconcile: {written} candle(s) written, {failed} failed day(s)", failed == 0
            )
        report = await runtime.stack.backfill.run(instruments, first, last)
        return HistoryOutcome(
            f"backfill: {report.chunks_fetched} chunk(s) fetched, "
            f"{report.candles_written} candle(s) written, "
            f"{report.days_already_covered} day(s) already covered, "
            f"{len(report.failed_chunks)} chunk(s) failed, {len(report.empty_days)} empty day(s)",
            report.ok,
        )


def _run_history(symbols: list[str], days: int, reconcile: bool) -> None:
    try:
        outcome = asyncio.run(_backfill(symbols, days, reconcile))
    except EmporosError as error:
        typer.secho(f"history failed: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(outcome.summary)
    if not outcome.complete:
        raise typer.Exit(code=2)  # incomplete: run again to resume


@history_app.command("backfill")
def history_backfill(symbols: list[str] = _SYMBOLS, days: int = _DAYS) -> None:
    """Backfill 1m history for the symbols. Resumable: re-run to continue after an interruption."""
    _run_history(symbols, days, reconcile=False)


_TIMEFRAME = typer.Option("5m", "--timeframe", "-t", help="The derived timeframe to store.")
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="First IST day.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last IST day (inclusive).")


async def _fetch_bars(
    symbols: list[str], timeframe: Timeframe, first: date, last: date
) -> HistoryOutcome:
    async with open_bar_fetch_runtime(Settings.default(), timeframe) as runtime:
        instruments = resolve_symbols(runtime.instruments, Exchange.NSE, symbols)
        earliest = SessionWindow().open_at(first)
        if runtime.hot_cutoff is not None and earliest < runtime.hot_cutoff:
            raise ConfigurationError(
                f"{first} is older than the hot retention for {timeframe.value} bars "
                f"({runtime.hot_cutoff.date()}), and no S3 bucket is configured to hold them"
            )
        report = await runtime.fetcher.run(instruments, first, last)
    full = 375 * 60 // int(timeframe.duration.total_seconds())
    short = sorted((i, d, n) for (i, d), n in report.bars_per_day.items() if n < full)
    lines = [
        f"fetch-bars {timeframe.value}: {report.chunks_fetched} chunk(s) fetched, "
        f"{report.bars_written} bar(s) written, {len(report.failed_chunks)} chunk(s) failed, "
        f"{len(report.bars_per_day)} instrument-day(s) with bars"
    ]
    lines += [f"  short day: {i} {d} has {n} of {full} bars" for i, d, n in short[:20]]
    return HistoryOutcome("\n".join(lines), report.ok)


@history_app.command("fetch-bars")
def history_fetch_bars(
    symbols: list[str] = _SYMBOLS,
    timeframe: str = _TIMEFRAME,
    first: datetime = _FROM,
    last: datetime = _TO,
) -> None:
    """Fetch 1m history, derive one timeframe (like the live pipeline) and store only that.

    For backtest data when no S3 bucket is configured: a year of 5m bars fits the hot tier.
    """
    try:
        frame = Timeframe(timeframe)
        if frame not in DERIVED_TIMEFRAMES:  # refuse before opening Mongo or the broker
            raise ConfigurationError(f"{timeframe} is not a timeframe derived from 1m bars")
        outcome = asyncio.run(_fetch_bars(symbols, frame, first.date(), last.date()))
    except (EmporosError, ValueError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"fetch-bars failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(outcome.summary)
    if not outcome.complete:
        raise typer.Exit(code=2)


@history_app.command("reconcile")
def history_reconcile(symbols: list[str] = _SYMBOLS, days: int = _DAYS) -> None:
    """Find gaps in stored history and re-fetch only those."""
    _run_history(symbols, days, reconcile=True)


@history_app.command("rollup")
def history_rollup(symbols: list[str] = _OPTIONAL_SYMBOLS) -> None:
    """Move candles older than each timeframe's hot retention from Mongo to S3 (run nightly)."""

    async def _rollup() -> HistoryOutcome:
        async with open_history_runtime(Settings.default()) as runtime:
            ids = None
            if symbols:
                found = resolve_symbols(runtime.instruments, Exchange.NSE, symbols)
                ids = [i.instrument_id for i in found]
            report = await runtime.stack.rollup.run(instrument_ids=ids)
            return HistoryOutcome(
                f"rollup: {report.archived} candle(s) archived, {report.deleted} removed from "
                f"the hot tier across {report.instruments} instrument(s), "
                f"{len(report.failures)} failure(s)",
                report.ok,
            )

    try:
        outcome = asyncio.run(_rollup())
    except EmporosError as error:
        typer.secho(f"rollup failed: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(outcome.summary)
    if not outcome.complete:
        raise typer.Exit(code=2)


@history_app.command("seed-calendar")
def history_seed_calendar(
    symbol: str = typer.Option("SBIN-EQ", help="A liquid reference symbol."),
    days: int = typer.Option(400, min=30, help="How many calendar days back to derive."),
) -> None:
    """Derive trading days from the broker's daily bars and store them in `market_calendar`."""

    async def _seed() -> int:
        async with open_history_runtime(Settings.default()) as runtime:
            (reference,) = resolve_symbols(runtime.instruments, Exchange.NSE, [symbol])
            first, last = _window(days)
            derived = await runtime.stack.seeder.seed(reference, first, last)
            return sum(1 for traded in derived.values() if not traded)

    try:
        holidays = asyncio.run(_seed())
    except EmporosError as error:
        typer.secho(f"calendar seed failed: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"calendar seeded; {holidays} weekday holiday(s) found")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
