"""`emporos research collect-fo-archive` — the NSE F&O bhavcopy archive to local Parquet (EM-225).

Only the exchange's publicly posted archive files, fetched from this development machine (never the
production host), one at a time and a few seconds apart, newest first, resumable through the ledger.
A 401, 403 or 429 stops the run (PROFIT_PLAN §8). Nothing goes to Atlas."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path

import httpx
import typer

from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.research.fo_archive_fetch import (
    ArchiveFetcher,
    ArchiveHalted,
    ArchiveRefused,
    FetchReport,
)
from emporos.research.fo_archive_layout import CutoverLayout
from emporos.research.fo_archive_store import FoDayStore, FoLedger
from emporos.research.fo_contract_specs import (
    ContractSpec,
    ContractSpecBuilder,
    InferenceCheck,
    LotSource,
    write_specs,
)

DEFAULT_FO_DIR = Path.home() / ".cache" / "emporos" / "fo_bhavcopy"
DEFAULT_FO_LEDGER = Path("docs/research/profit/fo-archive-ledger.jsonl")
EARLIEST_FO_DAY = datetime(2000, 6, 12)  # index futures began trading; nothing exists before

_FROM = typer.Option(EARLIEST_FO_DAY, formats=["%Y-%m-%d"], help="Oldest day to ask for.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Newest day to ask for (inclusive).")
_GAP = typer.Option(3.0, help="Seconds between requests (at least 1).")
_DIR = typer.Option(DEFAULT_FO_DIR, help="Where the per-day Parquet files go (local, not Atlas).")
_LEDGER = typer.Option(DEFAULT_FO_LEDGER, help="The append-only fetch ledger (URL, time, hash).")
_SPECS = typer.Option(
    None, help="Where to write the specs (default: <out>/contract_specs.parquet)."
)


async def _collect(first: date, last: date, gap: float, out: Path, ledger: Path) -> FetchReport:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        fetcher = ArchiveFetcher(
            client, CutoverLayout(), FoDayStore(out), FoLedger(ledger), SystemClock(),
            AsyncioSleeper(), gap, progress=typer.echo,
        )  # fmt: skip
        return await fetcher.run(first, last)


def research_collect_fo_archive(
    first: datetime = _FROM,
    last: datetime = _TO,
    seconds_between_requests: float = _GAP,
    out: Path = _DIR,
    ledger: Path = _LEDGER,
) -> None:
    """Collect the NSE F&O daily bhavcopy (index futures and options) newest day first."""
    try:
        report = asyncio.run(
            _collect(first.date(), last.date(), seconds_between_requests, out, ledger)
        )
    except (ArchiveRefused, ArchiveHalted) as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except ValueError as error:
        typer.secho(f"collect-fo-archive failed: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{report.fetched} fetched ({report.rows} rows), {report.absent} absent, "
        f"{report.skipped} already done, {len(report.failed)} failed"
    )
    if report.failed:
        raise typer.Exit(code=2)


def research_build_fo_specs(
    out: Path = _DIR,
    target: Path | None = _SPECS,
) -> None:
    """Write the lot size and expiry calendar in force on each stored day, and report where the
    turnover inference disagrees with the exchange's own lot size."""
    store = FoDayStore(out)
    builder = ContractSpecBuilder()
    specs: list[ContractSpec] = []
    checks: list[InferenceCheck] = []
    for day in store.days():
        rows = store.read(day)
        specs.extend(builder.build(rows))
        checks.extend(builder.check(rows))
    if not specs:
        typer.secho("no stored days: run collect-fo-archive first", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    path = target or out / "contract_specs.parquet"
    write_specs(path, specs)
    unknown = sum(1 for s in specs if s.lot_source is LotSource.UNKNOWN)
    typer.echo(
        f"{len(specs)} spec(s) over {len(store.days())} day(s) to {path}; "
        f"{unknown} without a lot size"
    )
    disagree = [c for c in checks if not c.agrees]
    typer.echo(
        f"inference vs exchange lot size: {len(checks) - len(disagree)} agree, "
        f"{len(disagree)} differ"
    )
    for c in disagree[:20]:
        typer.echo(f"  {c.day} {c.symbol}: exchange {c.exchange}, inferred {c.inferred}")
