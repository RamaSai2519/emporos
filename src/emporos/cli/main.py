"""The `emporos` CLI.

The Next.js dashboard is the primary operator interface (Decision 12) — this
CLI exists for local development, CI, and break-glass operations when the
dashboard is unreachable. Every command below is a placeholder wired up in
Phase 1 (EM-9); each is implemented by the phase noted in its docstring.
"""

from __future__ import annotations

import asyncio

import typer

from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.persistence.migrations import MigrationReport, MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.schema import PLATFORM_SCHEMA

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


@instruments_app.command("sync")
def instruments_sync() -> None:
    """Download and apply the latest Angel One instrument master."""
    _not_implemented("instruments sync", "EM-29")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
