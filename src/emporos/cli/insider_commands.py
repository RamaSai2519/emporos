"""`emporos research collect-insider` and `build-insider` (EM-244, atlas plan §3.2 item 4).

`collect-insider` asks NSE's public disclosure feeds (SEBI PIT insider trades and pledges; SAST
Reg 29 holder disclosures) for one calendar month at a time, one request about 3 s apart, keeps each
reply verbatim and ledgers it with its URL and fetch time; a refusal (401, 403, 429) stops the run.
`build-insider` turns the kept replies into two Parquet tables timed by the exchange's own
dissemination timestamp. Development machine only."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path

import httpx
import typer

from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.errors import EmporosError
from emporos.core.paths import research_dir
from emporos.research.cause_ledger.insider import monthly_pages
from emporos.research.cause_ledger.insider_store import InsiderLedger
from emporos.research.cause_ledger.pages import PageCollector
from emporos.research.cause_ledger.sources import CALENDAR_FIRST, CALENDAR_LAST
from emporos.research.filings.collector import CollectionHalted
from emporos.research.filings.polite import PoliteGet, SourceRefused
from emporos.research.filings.raw_store import FetchLedger

DEFAULT_INSIDER_RAW = research_dir() / "insider" / "raw"
DEFAULT_INSIDER_OUT = research_dir() / "insider_v1"
DEFAULT_INSIDER_LEDGER = Path("docs/research/profit/insider-ledger.jsonl")

_RAW = typer.Option(DEFAULT_INSIDER_RAW, help="Where replies are kept verbatim (not git).")
_OUT = typer.Option(DEFAULT_INSIDER_OUT, help="Where the Parquet tables go.")
_LEDGER = typer.Option(DEFAULT_INSIDER_LEDGER, help="The append-only fetch ledger.")
_FROM = typer.Option(
    datetime(CALENDAR_FIRST.year, CALENDAR_FIRST.month, 1), formats=["%Y-%m-%d"], help="First day."
)
_TO = typer.Option(
    datetime(CALENDAR_LAST.year, CALENDAR_LAST.month, CALENDAR_LAST.day), "--to",
    formats=["%Y-%m-%d"], help="Last day (in).",
)  # fmt: skip
_GAP = typer.Option(3.0, help="Seconds between requests (at least 1).")


async def _collect(raw: Path, ledger: Path, first: date, last: date, gap: float) -> list[str]:
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=False) as client:
        collector = PageCollector(
            PoliteGet(client, AsyncioSleeper(), gap), raw, FetchLedger(ledger), SystemClock()
        )
        return await collector.run(monthly_pages(first, last), typer.echo)


def research_collect_insider(
    raw: Path = _RAW,
    ledger: Path = _LEDGER,
    first: datetime = _FROM,
    last: datetime = _TO,
    seconds_between_requests: float = _GAP,
) -> None:
    """Fetch the monthly PIT and SAST disclosure replies (resumable, polite)."""
    try:
        failed = asyncio.run(
            _collect(raw, ledger, first.date(), last.date(), seconds_between_requests)
        )
    except SourceRefused as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except CollectionHalted as error:
        typer.secho(f"halted: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"collect-insider failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    if failed:
        typer.echo(f"re-run to retry: {'; '.join(failed)}")
        raise typer.Exit(code=2)


def research_build_insider(
    raw: Path = _RAW, out: Path = _OUT, first: datetime = _FROM, last: datetime = _TO
) -> None:
    """Write pit.parquet and sast.parquet from the kept replies."""
    try:
        summary = InsiderLedger(raw, out).build(first.date(), last.date())
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"build-insider failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{summary.pit_rows} PIT rows, {summary.sast_rows} SAST rows "
        f"({summary.first} .. {summary.last}) in {out}"
    )
