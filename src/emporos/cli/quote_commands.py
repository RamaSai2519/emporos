"""`emporos quotes summary`: is the L1 quote recorder recording? (EM-217). Local Parquet only."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import typer

from emporos.cli.quote_recording import DEFAULT_QUOTES_DIR
from emporos.core.clock import IST, SystemClock
from emporos.quotes.summary import summarize_day

quotes_app = typer.Typer(help="Recorded L1 quotes.", no_args_is_help=True)

_DAY = typer.Option(None, formats=["%Y-%m-%d"], help="IST day to summarise (default: today).")
_DIR = typer.Option(DEFAULT_QUOTES_DIR, help="Where recorded quotes are.")


@quotes_app.command("summary")
def quotes_summary(day: datetime | None = _DAY, directory: Path = _DIR) -> None:
    """Rows, instruments, polls, receive times, two-sided share and median spread for one day."""
    chosen: date = day.date() if day else SystemClock().now().astimezone(IST).date()
    for line in summarize_day(directory, chosen).lines():
        typer.echo(line)
