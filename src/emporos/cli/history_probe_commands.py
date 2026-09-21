"""`emporos history probe-depth` — how far back the broker's history goes, found by asking.

Read-only: it logs in to the broker (which supersedes any other session for the client code) and
makes a few dozen history requests for one liquid instrument; it writes nothing to the database.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import typer

from emporos.cli.history_composition import resolve_symbols
from emporos.cli.history_runtime import open_broker_history_runtime
from emporos.core.clock import IST
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Timeframe
from emporos.domain.instruments import Exchange
from emporos.history.depth import HistoryDepthProbe, ProbeInconclusiveError, ProbeReport

_SYMBOL = typer.Option("SBIN-EQ", "--symbol", "-s", help="A long-listed, liquid symbol.")
_TIMEFRAMES = typer.Option(
    ["1m", "5m", "15m", "1h", "1d"], "--timeframe", "-t", help="Interval to probe (repeatable)."
)
_AS_OF = typer.Option(
    None, "--as-of", formats=["%Y-%m-%d"], help="Last day asked for (default: last weekday)."
)


def _last_closed_weekday(now: datetime) -> date:
    day = now.astimezone(IST).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def render(report: ProbeReport) -> str:
    lines = [
        f"history depth for {report.instrument}, as of {report.as_of}",
        "",
        "| interval | longest request served whole | first request cut short | oldest bar "
        "| calls |",
        "|---|---|---|---|---|",
    ]
    for f in report.findings:
        whole = "-" if f.span.largest_whole_days is None else f"{f.span.largest_whole_days} d"
        cut = (
            "none up to the ladder's top"
            if f.span.first_truncated_days is None
            else (f"{f.span.first_truncated_days} d")
        )
        oldest = "no data" if f.depth.earliest_day is None else str(f.depth.earliest_day)
        lines.append(f"| {f.span.timeframe.value} | {whole} | {cut} | {oldest} | {f.calls} |")
    return "\n".join(lines)


async def _probe(symbol: str, timeframes: list[str], as_of: date) -> ProbeReport:
    frames = [Timeframe(t) for t in timeframes]
    async with open_broker_history_runtime(Settings.default()) as runtime:
        (instrument,) = resolve_symbols(runtime.instruments, Exchange.NSE, [symbol])
        return await HistoryDepthProbe(runtime.source, instrument).probe(frames, as_of)


def history_probe_depth(
    symbol: str = _SYMBOL,
    timeframes: list[str] = _TIMEFRAMES,
    as_of: datetime | None = _AS_OF,
) -> None:
    """Find the broker's per-request span and maximum history per interval (read-only)."""
    day = as_of.date() if as_of else _last_closed_weekday(datetime.now(IST))
    try:
        report = asyncio.run(_probe(symbol, timeframes, day))
    except (EmporosError, ValueError, ProbeInconclusiveError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"probe failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(render(report))
