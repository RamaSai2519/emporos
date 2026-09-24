"""`emporos history fetch-reference` — index and volatility candles for research (EM-191 D2).

Read-only against the broker's public master and its history endpoint; the bars go to the same
candle store as everything else. Resumable: re-running skips chunks already recorded as fetched."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

import httpx
import typer

from emporos.cli.history_runtime import open_bar_fetch_runtime
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Timeframe
from emporos.history.reference_fetch import ReferenceBarFetch
from emporos.instruments.downloader import MASTER_URL
from emporos.instruments.reference_series import ReferenceSeriesCatalog
from emporos.marketdata.timeframes import DERIVED_TIMEFRAMES

_SERIES = typer.Option(
    None, "--series", help="A declared series' symbol, e.g. 'Nifty 50' (repeatable; default all)."
)
_TIMEFRAME = typer.Option("5m", "--timeframe", "-t", help="The derived timeframe to store.")
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="First IST day.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last IST day (inclusive).")


async def _master_rows() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(MASTER_URL)
        response.raise_for_status()
        rows: list[dict[str, Any]] = response.json()
        return rows


async def _fetch(series: list[str], frame: Timeframe, first: date, last: date) -> tuple[str, bool]:
    rows = await _master_rows()
    async with open_bar_fetch_runtime(Settings.default(), frame) as runtime:
        chosen, report = await ReferenceBarFetch(
            ReferenceSeriesCatalog.load(), runtime.fetcher
        ).run(rows, first, last, series)
        rejected = len(runtime.rejected.bars)
    summary = (
        f"fetch-reference {frame.value}: {len(chosen)} series, {report.chunks_fetched} chunk(s) "
        f"fetched, {report.chunks_skipped} already recorded, {report.bars_written} bar(s) written, "
        f"{len(report.failed_chunks)} chunk(s) failed, {rejected} invalid bar(s) skipped"
    )
    return summary, report.ok


def history_fetch_reference(
    series: list[str] = _SERIES,
    timeframe: str = _TIMEFRAME,
    first: datetime = _FROM,
    last: datetime = _TO,
) -> None:
    """Fetch the declared index and INDIA VIX series (config/reference_series.yaml) as candles."""
    try:
        frame = Timeframe(timeframe)
        if frame not in DERIVED_TIMEFRAMES:
            raise ValueError(f"{timeframe} is not a timeframe derived from 1m bars")
        summary, ok = asyncio.run(_fetch(series or [], frame, first.date(), last.date()))
    except (EmporosError, ValueError, httpx.HTTPError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"fetch-reference failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(summary)
    if not ok:
        raise typer.Exit(code=2)
