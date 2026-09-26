"""`emporos research collect-index-notices` — NSE Indices press releases for A-F3 (EM-224).

Fetches the public press-release listing (one request), writes it as a CSV, selects the notices
whose title says a constituent list may have changed, and downloads each PDF once, three seconds
apart, stopping at the first 401, 403 or 429. Each file is recorded with its URL, fetch date and
hash in `docs/research/profit/index-notices.jsonl`; the PDFs stay in a local cache directory.
Development machine only, never the production host (PROFIT_PLAN §8)."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path

import httpx
import typer

from emporos.core.clock import AsyncioSleeper, Sleeper, SystemClock
from emporos.core.paths import research_dir
from emporos.research.index_notices import (
    IndexNoticeCollector,
    NoticeRefused,
    NoticeStore,
    NseIndicesSource,
    parse_listing,
    select_candidates,
)

PROFIT_DIR = Path("docs/research/profit")
DEFAULT_LISTING = PROFIT_DIR / "index-notices-listing.csv"
DEFAULT_PROVENANCE = PROFIT_DIR / "index-notices.jsonl"
DEFAULT_CACHE = research_dir() / "index-notices"

_LISTING = typer.Option(DEFAULT_LISTING, help="Where the notice listing is written.")
_PROVENANCE = typer.Option(DEFAULT_PROVENANCE, help="The append-only fetch record.")
_CACHE = typer.Option(DEFAULT_CACHE, help="Where the PDFs are kept (local, not committed).")
_GAP = typer.Option(3.0, help="Seconds between requests (at least 1).")


async def _collect(
    listing: Path, provenance: Path, cache: Path, gap: float, sleeper: Sleeper
) -> tuple[int, int, list[str]]:
    today = SystemClock().now().date()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        source = NseIndicesSource(client, sleeper, gap)
        refs = parse_listing(await source.listing())
        listing.parent.mkdir(parents=True, exist_ok=True)
        with listing.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["notice_date", "url", "title", "fetched_on"])
            writer.writerows((r.notice_date, r.url, r.title, today) for r in refs)
        candidates = select_candidates(refs)
        fetched, failed = await IndexNoticeCollector(
            source, NoticeStore(cache, provenance), today
        ).run(candidates)
    return len(refs), fetched, failed


def research_collect_index_notices(
    listing: Path = _LISTING,
    provenance: Path = _PROVENANCE,
    cache: Path = _CACHE,
    seconds_between_requests: float = _GAP,
) -> None:
    """Fetch the index-change notices (PDFs) that can affect NIFTY 100 / Midcap 150 membership."""
    try:
        listed, fetched, failed = asyncio.run(
            _collect(listing, provenance, cache, seconds_between_requests, AsyncioSleeper())
        )
    except NoticeRefused as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except (ValueError, OSError, httpx.HTTPError) as error:
        typer.secho(f"collect-index-notices failed: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"{listed} notices listed; {fetched} PDF(s) fetched; {len(failed)} failed")
    if failed:
        typer.echo("re-run to retry: " + ", ".join(failed))
        raise typer.Exit(code=2)
