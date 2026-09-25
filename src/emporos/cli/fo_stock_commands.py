"""`emporos research collect-fo-stock-archive` and `report-fo-stock-archive` (EM-241).

The same public NSE bhavcopy files as `collect-fo-archive`, fetched by the same polite fetcher (one
file at a time, a few seconds apart, newest first, resumable, a 401, 403 or 429 stops the run, the
URL and hash of every file on record), but keeping single-stock futures and options in their own
dataset (`fo_stock_v1`) and ledger. Development machine only; nothing goes to Atlas."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path

import httpx
import typer

from emporos.cli.fo_archive_commands import EARLIEST_FO_DAY
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.research.fo_archive_fetch import (
    ArchiveFetcher,
    ArchiveHalted,
    ArchiveRefused,
    FetchReport,
)
from emporos.research.fo_archive_layout import CutoverLayout
from emporos.research.fo_archive_store import FoDayStore, FoLedger
from emporos.research.fo_stock_archive import DATASET, StockArchiveReader
from emporos.research.fo_stock_report import StockArchiveReporter

DEFAULT_STOCK_DIR = Path.home() / ".cache" / "emporos" / DATASET
DEFAULT_STOCK_LEDGER = Path("docs/research/profit/fo-stock-ledger.jsonl")

_FROM = typer.Option(EARLIEST_FO_DAY, formats=["%Y-%m-%d"], help="Oldest day to ask for.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Newest day to ask for (inclusive).")
_GAP = typer.Option(3.0, help="Seconds between requests (at least 1).")
_DIR = typer.Option(DEFAULT_STOCK_DIR, help="Where the per-day Parquet files go (local).")
_LEDGER = typer.Option(DEFAULT_STOCK_LEDGER, help="The append-only fetch ledger.")


async def _collect(first: date, last: date, gap: float, out: Path, ledger: Path) -> FetchReport:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        fetcher = ArchiveFetcher(
            client, CutoverLayout(), FoDayStore(out), FoLedger(ledger), SystemClock(),
            AsyncioSleeper(), gap, progress=typer.echo, reader=StockArchiveReader(),
            what="stock contract",
        )  # fmt: skip
        return await fetcher.run(first, last)


def research_collect_fo_stock_archive(
    first: datetime = _FROM,
    last: datetime = _TO,
    seconds_between_requests: float = _GAP,
    out: Path = _DIR,
    ledger: Path = _LEDGER,
) -> None:
    """Collect stock futures and options (monthlies, strikes within 15% of the underlying)."""
    try:
        report = asyncio.run(
            _collect(first.date(), last.date(), seconds_between_requests, out, ledger)
        )
    except (ArchiveRefused, ArchiveHalted) as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except ValueError as error:
        typer.secho(f"collect-fo-stock-archive failed: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{report.fetched} fetched ({report.rows} rows), {report.absent} absent, "
        f"{report.skipped} already done, {len(report.failed)} failed"
    )
    if report.failed:
        raise typer.Exit(code=2)


def research_report_fo_stock_archive(
    first: datetime = _FROM,
    last: datetime = _TO,
    out: Path = _DIR,
    ledger: Path = _LEDGER,
) -> None:
    """Files, rows, missing days and every lot-size change in the stock dataset."""
    report = StockArchiveReporter(FoDayStore(out), FoLedger(ledger)).build(
        first.date(), last.date()
    )
    typer.echo(
        f"{report.files} file(s), {report.rows} rows, {report.first_day} .. {report.last_day}"
    )
    typer.echo(f"{len(report.holidays)} weekday(s) with no file at the exchange (404)")
    never = ", ".join(str(d) for d in report.unfetched[:20])
    typer.echo(f"{len(report.unfetched)} weekday(s) never fetched: {never}")
    typer.echo(
        f"{len(report.lot_changes)} lot-size change(s) in the exchange's own numbers; "
        f"{report.symbols_without_exchange_lot} name(s) with no exchange lot size at all"
    )
    for change in report.lot_changes:
        typer.echo(f"  {change.symbol}: {change.old} -> {change.new} from {change.first_seen}")
