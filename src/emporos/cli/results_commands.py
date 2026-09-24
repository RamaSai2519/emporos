"""`emporos research collect-results` — when each D1 name's results became public (EM-191 D5).

Reads the committed constituent lists and asks the exchange's announcements feed, one polite
request per name, for its results filings over the whole window. Filings go to an append-only
ledger with the exchange's own timestamp; a name already collected is skipped, so an interrupted
run resumes. No prices are read and nothing here decides a reaction day."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path

import httpx
import typer

from emporos.cli.history_universe_commands import DEFAULT_LIST_DIR, LISTS
from emporos.core.clock import AsyncioSleeper, Clock, Sleeper, SystemClock
from emporos.research.nse_announcements import (
    AnnouncementRefused,
    AnnouncementSource,
    NseAnnouncementSource,
)
from emporos.research.results_filings import FilingLedger
from emporos.research.universe_lists import ConstituentList

DEFAULT_EVENTS_DIR = Path("docs/research/edge-search/events")
FIRST_DAY = date(2016, 10, 3)

_DIR = typer.Option(DEFAULT_LIST_DIR, help="The directory holding the committed constituent lists.")
_OUT = typer.Option(DEFAULT_EVENTS_DIR, help="Where the append-only filing ledger lives.")
_FROM = typer.Option(FIRST_DAY, formats=["%Y-%m-%d"], help="First day to ask for.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last day to ask for (inclusive).")
_GAP = typer.Option(3.0, help="Seconds between requests (at least 1).")


class ResultsCollection:
    """Collect, name by name, what the ledger does not hold yet."""

    def __init__(self, source: AnnouncementSource, ledger: FilingLedger, clock: Clock) -> None:
        self._source = source
        self._ledger = ledger
        self._clock = clock

    async def run(self, symbols: list[str], first: date, last: date) -> tuple[int, list[str]]:
        """(filings added, names that failed). A refusal from the exchange propagates."""
        done = self._ledger.collected_symbols()
        added, failed = 0, []
        for symbol in symbols:
            if symbol in done:
                continue
            try:
                filings = await self._source.results_filings(symbol, first, last)
            except AnnouncementRefused:
                raise
            except (httpx.HTTPError, ValueError) as error:
                failed.append(symbol)
                typer.echo(f"{symbol}: failed ({error})")
                continue
            new = self._ledger.record(symbol, filings, first, last, self._clock.now())
            added += new
            typer.echo(f"{symbol}: {len(filings)} results filing(s), {new} new")
        return added, failed


def constituent_symbols(directory: Path) -> list[str]:
    seen: list[str] = []
    for name, index in LISTS.items():
        for row in ConstituentList.load(index, directory / name).rows:
            if row.symbol not in seen:
                seen.append(row.symbol)
    return seen


async def _collect(
    directory: Path, out: Path, first: date, last: date, gap: float, sleeper: Sleeper
) -> tuple[int, list[str]]:
    ledger = FilingLedger(out / "results-filings.jsonl", out / "results-collected.jsonl")
    async with httpx.AsyncClient(timeout=30.0) as client:
        source = NseAnnouncementSource(client, sleeper, gap)
        return await ResultsCollection(source, ledger, SystemClock()).run(
            constituent_symbols(directory), first, last
        )


def research_collect_results(
    directory: Path = _DIR,
    out: Path = _OUT,
    first: datetime = _FROM,
    last: datetime = _TO,
    seconds_between_requests: float = _GAP,
) -> None:
    """Collect results-filing timestamps for the D1 names from the exchange's announcements feed."""
    try:
        added, failed = asyncio.run(
            _collect(directory, out, first.date(), last.date(), seconds_between_requests,
                     AsyncioSleeper())
        )  # fmt: skip
    except AnnouncementRefused as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except ValueError as error:
        typer.secho(f"collect-results failed: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"{added} new filing(s); {len(failed)} name(s) failed")
    if failed:
        typer.echo(f"re-run to retry: {', '.join(failed)}")
        raise typer.Exit(code=2)
