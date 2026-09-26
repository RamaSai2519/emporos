"""`emporos research build-iv`: the daily IV and implied-move dataset (EM-244, atlas plan §7a).

Reads the local F&O archive one day at a time (stock options from `fo_stock_v1`, or the index
archive `fo_bhavcopy`), computes Black-76 implied volatilities from that day's settle prices with
the RBI repo rate from the macro calendar, and writes `<research dir>/iv_v1/<source>/<year>/<date>`
Parquet files. A day already built is skipped. Nothing is fetched."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import typer

from emporos.core.paths import research_dir
from emporos.research.cause_ledger.events import DEFAULT_CALENDAR_FILE, YamlCalendar
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.iv.dataset import IvBuilder, IvStore
from emporos.research.iv.rates import CalendarRepoRate

SOURCES = {"stock": "fo_stock_v1", "index": "fo_bhavcopy"}

_SOURCE = typer.Option("stock", help="`stock`: fo_stock_v1. `index`: the index F&O archive.")
_FROM = typer.Option(datetime(2024, 1, 1), formats=["%Y-%m-%d"], help="First day.")
_TO = typer.Option(datetime(2026, 3, 18), "--to", formats=["%Y-%m-%d"], help="Last day (in).")
_CALENDAR = typer.Option(DEFAULT_CALENDAR_FILE, help="The macro calendar (repo rates).")
_OUT = typer.Option(None, help="Output directory (default: <research dir>/iv_v1/<source>).")
_IN = typer.Option(None, help="The F&O archive directory (default: <research dir>/<dataset>).")


def research_build_iv(
    source: str = _SOURCE,
    first: datetime = _FROM,
    last: datetime = _TO,
    calendar: Path = _CALENDAR,
    out: Path | None = _OUT,
    archive: Path | None = _IN,
) -> None:
    """Build the IV dataset for every archived day in [first, last] not yet built."""
    if source not in SOURCES:
        typer.secho(f"unknown source {source!r}: use {sorted(SOURCES)}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    days_store = FoDayStore(archive or research_dir() / SOURCES[source])
    target = out or research_dir() / "iv_v1" / source
    store = IvStore(target)
    try:
        builder = IvBuilder(CalendarRepoRate(YamlCalendar(calendar).read()))
    except (ValueError, OSError, KeyError) as error:
        typer.secho(f"build-iv failed: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    built = skipped = rows = 0
    for day in _days(days_store.days(), first.date(), last.date()):
        if store.has(day):
            skipped += 1
            continue
        rows += store.write(day, builder.build_day(day, days_store.read(day)))
        built += 1
    typer.echo(f"{built} day(s) built ({rows} rows), {skipped} already held, into {target}")


def _days(days: list[date], first: date, last: date) -> list[date]:
    return [d for d in days if first <= d <= last]
