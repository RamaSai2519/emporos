"""The `emporos` CLI.

The Next.js dashboard is the primary operator interface (Decision 12) — this
CLI exists for local development, CI, and break-glass operations when the
dashboard is unreachable. Every command below is a placeholder wired up in
Phase 1 (EM-9); each is implemented by the phase noted in its docstring.
"""

from __future__ import annotations

import asyncio

import httpx
import typer

from emporos.core.alerts import LogAlertSink
from emporos.core.clock import SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.differ import InstrumentDiffer
from emporos.instruments.downloader import MASTER_URL, InstrumentMasterDownloader
from emporos.instruments.store import MongoInstrumentMasterStore
from emporos.instruments.sync import InstrumentSyncService, SyncOutcome, SyncResult
from emporos.instruments.validator import InstrumentMasterValidator
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
app.add_typer(instruments_app, name="instruments")


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
def backtest() -> None:
    """Run a strategy against historical data."""
    _not_implemented("backtest", "Phase 10")


@app.command()
def backfill() -> None:
    """Backfill historical candles for the instrument universe."""
    _not_implemented("backfill", "EM-55")


@app.command()
def halt() -> None:
    """Trip the kill switch. Halts all trading immediately (plan.md §11)."""
    _not_implemented("halt", "EM-74")


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
