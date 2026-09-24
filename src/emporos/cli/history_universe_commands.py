"""`emporos history fetch-universe` — the D1 wider universe as 5m candles (EM-191, EM-208).

Reads the committed NSE constituent lists, resolves each name against the instrument master, and
fetches history in small batches through the same resumable fetcher `fetch-bars` uses. Bars older
than the hot retention go straight to the cold Parquet tier, so Mongo only ever holds the last two
weeks. Names the master does not know are reported and skipped. Re-running continues where an
interrupted run stopped: chunks already recorded as fetched are not asked for again."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import typer

from emporos.cli.history_runtime import open_bar_fetch_runtime
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Timeframe
from emporos.domain.instruments import (
    Exchange,
    Instrument,
    InstrumentResolver,
    UnknownInstrumentError,
)
from emporos.marketdata.timeframes import DERIVED_TIMEFRAMES
from emporos.research.universe_lists import ConstituentList, combined_symbols

DEFAULT_LIST_DIR = Path("config/universe/d1")
_LISTS = {"nifty100.csv": "NIFTY 100", "niftymidcap150.csv": "NIFTY Midcap 150"}

_DIR = typer.Option(DEFAULT_LIST_DIR, help="The directory holding the committed constituent lists.")
_TIMEFRAME = typer.Option("5m", "--timeframe", "-t", help="The derived timeframe to store.")
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="First IST day.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last IST day (inclusive).")
_BATCH = typer.Option(10, help="Names per batch; a batch is one fetcher run and one progress line.")
_SKIP = typer.Option(0, help="Skip this many names first (resume a stopped run by hand).")


def universe_symbols(directory: Path) -> list[str]:
    lists = [ConstituentList.load(index, directory / name) for name, index in _LISTS.items()]
    return combined_symbols(lists)


def resolve_known(
    resolver: InstrumentResolver, symbols: Sequence[str]
) -> tuple[list[Instrument], list[str]]:
    """(instruments the master knows, symbols it does not)."""
    known: list[Instrument] = []
    unknown: list[str] = []
    for symbol in symbols:
        try:
            known.append(resolver.by_symbol(Exchange.NSE, symbol))
        except UnknownInstrumentError:
            unknown.append(symbol)
    return known, unknown


def batches(items: Sequence[Instrument], size: int) -> list[Sequence[Instrument]]:
    if size < 1:
        raise ValueError("a batch holds at least one name")
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _fetch(
    directory: Path, frame: Timeframe, first: date, last: date, size: int, skip: int
) -> tuple[list[str], bool]:
    symbols = universe_symbols(directory)[skip:]
    lines: list[str] = []
    complete = True
    async with open_bar_fetch_runtime(Settings.default(), frame) as runtime:
        instruments, unknown = resolve_known(runtime.instruments, symbols)
        lines.append(f"{len(symbols)} name(s) listed, {len(instruments)} known to the master")
        if unknown:
            lines.append(f"unknown to the master, skipped: {', '.join(unknown)}")
        for number, batch in enumerate(batches(instruments, size), start=1):
            report = await runtime.fetcher.run(list(batch), first, last)
            complete = complete and report.ok
            names = ", ".join(i.tradingsymbol for i in batch)
            line = (
                f"batch {number}: {report.chunks_fetched} chunk(s), {report.bars_written} bar(s), "
                f"{len(report.failed_chunks)} failed [{names}]"
            )
            lines.append(line)
            typer.echo(line)  # progress must be visible while a long run is still going
    return lines, complete


def history_fetch_universe(
    directory: Path = _DIR,
    timeframe: str = _TIMEFRAME,
    first: datetime = _FROM,
    last: datetime = _TO,
    batch_size: int = _BATCH,
    skip: int = _SKIP,
) -> None:
    """Fetch the D1 universe (NIFTY 100 + NIFTY Midcap 150, current constituents) as candles."""
    try:
        frame = Timeframe(timeframe)
        if frame not in DERIVED_TIMEFRAMES:
            raise ValueError(f"{timeframe} is not a timeframe derived from 1m bars")
        lines, complete = asyncio.run(
            _fetch(directory, frame, first.date(), last.date(), batch_size, skip)
        )
    except (EmporosError, ValueError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"fetch-universe failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(lines[0])
    if not complete:
        typer.echo("some chunks failed: re-run the same command to retry only those")
        raise typer.Exit(code=2)
