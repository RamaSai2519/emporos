"""`emporos broker-verify ...`: record and show broker verification results (EM-186)."""

from __future__ import annotations

import asyncio
import getpass
from collections.abc import Awaitable, Callable

import typer

from emporos.cli.broker_verification_console import BrokerVerificationConsole
from emporos.core.clock import SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.core.ids import IdGenerator
from emporos.graduation.config import GraduationSettingsLoader
from emporos.persistence.broker_verification_store import MongoBrokerVerificationLog
from emporos.persistence.collections import Collection
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.schema import PLATFORM_SCHEMA, Schema

broker_verify_app = typer.Typer(
    help="Record and show the results of the checks that gate live trading.",
    no_args_is_help=True,
)


async def _with_console(
    action: Callable[[BrokerVerificationConsole], Awaitable[list[str]]],
) -> list[str]:
    mongo = MongoClientFactory(Settings.default())
    try:
        database = mongo.database()
        # Only this collection's own spec: `db migrate` is the whole schema and is not needed here.
        only = Schema((PLATFORM_SCHEMA.spec_for(Collection.BROKER_VERIFICATION_CHECKS),))
        await MigrationRunner(MongoSchemaStore(database), only).apply()
        max_age = GraduationSettingsLoader().load().broker_verification_max_age
        return await action(
            BrokerVerificationConsole(
                MongoBrokerVerificationLog(database, IdGenerator()), SystemClock(), max_age
            )
        )
    finally:
        await mongo.close()


def _run(action: Callable[[BrokerVerificationConsole], Awaitable[list[str]]]) -> None:
    try:
        lines = asyncio.run(_with_console(action))
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"broker-verify failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in lines:
        typer.echo(line)


@broker_verify_app.command("record")
def record(
    check: str = typer.Argument(..., help="Check name, e.g. login_and_session."),
    outcome: str = typer.Argument(..., help="pass | fail | unverified | blocked."),
    evidence: str = typer.Option(..., "--evidence", help="Where the proof lives (test, log, doc)."),
    detail: str = typer.Option("", "--detail", help="One sentence: what was actually done."),
    by: str = typer.Option("", "--by", help="Who recorded it (defaults to the OS user)."),
) -> None:
    """Append one result. Nothing is edited; the latest result per check is what counts."""
    _run(lambda c: c.record(check, outcome, evidence, detail, by or getpass.getuser()))


@broker_verify_app.command("show")
def show(
    history: bool = typer.Option(False, "--history", help="Every result, oldest first."),
) -> None:
    """The latest result of every critical check (missing ones are UNVERIFIED)."""
    _run(lambda c: c.show(history=history))
