"""`emporos backtest cache` — the local copy of closed candle months that makes backtests fast.

The cache is derived data: `clear` only makes the next backtest slower, never wrong. `warm` reads
every month a strategy's universe and window need, once, several instruments at a time, so the
runs that follow never wait on Atlas.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import typer

from emporos.backtest.engine import DEFAULT_WARMUP_LOOKBACK
from emporos.cli.backtest_runtime import candle_cache_root, open_backtest_runtime
from emporos.cli.strategy_composition import build_registry
from emporos.core.clock import IST
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Timeframe
from emporos.history.reference_fetch import ReferenceCacheWarm
from emporos.instruments.reference_series import ReferenceSeriesCatalog
from emporos.persistence.candle_cache import CandleCacheFiles
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.resolution import StrategyConfigResolver

cache_app = typer.Typer(help="The local candle cache used by backtests.", no_args_is_help=True)

_STRATEGIES = typer.Argument(..., exists=True, dir_okay=False, help="Strategy YAML file(s).")
_FIRST = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="First trading day.")
_LAST = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last trading day (inclusive).")
_PARALLEL = typer.Option(
    2, min=1, help="Instruments fetched at once. The dev Atlas stalls at 4 or more large reads."
)


@cache_app.command("status")
def cache_status() -> None:
    """Where the cache is, how much it holds, and its fingerprint."""
    files = CandleCacheFiles(candle_cache_root(Settings.default()))
    typer.echo(
        f"{files.root}\n{len(files.files())} files, {files.size() / 1_048_576:.1f} MiB\n"
        f"{files.fingerprint()}"
    )


@cache_app.command("clear")
def cache_clear() -> None:
    """Delete every cached file. The next backtest re-reads from Atlas (and refills it)."""
    removed = CandleCacheFiles(candle_cache_root(Settings.default())).clear()
    typer.echo(f"removed {removed} cached month files")


async def _warm(
    strategies: list[Path], first: datetime, last: datetime, parallel: int
) -> tuple[int, int]:
    registry = build_registry()
    start = datetime.combine(first.date(), time(0, 0), tzinfo=IST).astimezone(UTC)
    end = datetime.combine(last.date() + timedelta(days=1), time(0, 0), tzinfo=IST).astimezone(UTC)
    async with open_backtest_runtime(Settings.default()) as runtime:
        universe = runtime.instruments.as_of(start, assume_earliest_before_history=True)
        loader = StrategyConfigLoader(StrategyConfigResolver(registry, universe.resolver))
        wanted = {
            (instrument, config.timeframe)
            for config in (loader.load_file(path) for path in strategies)
            for instrument in config.instrument_ids
        }
        gate = asyncio.Semaphore(parallel)
        bars = 0

        async def read(instrument: str, timeframe) -> None:  # type: ignore[no-untyped-def]
            nonlocal bars
            async with gate:
                # warming copies bars into the cache and produces no result, so it reads the
                # cache directly rather than through the vault
                got = await (runtime.cache or runtime.reader).get_range(
                    instrument, timeframe, start - DEFAULT_WARMUP_LOOKBACK, end
                )
            bars += len(got)
            typer.echo(f"  {instrument}: {len(got)} bars")

        await asyncio.gather(*(read(i, t) for i, t in sorted(wanted, key=str)))
    return len(wanted), bars


@cache_app.command("warm")
def cache_warm(
    strategies: list[Path] = _STRATEGIES,
    first: datetime = _FIRST,
    last: datetime = _LAST,
    parallel: int = _PARALLEL,
) -> None:
    """Read every month the strategies' universes need for FROM..TO into the cache."""
    try:
        instruments, bars = asyncio.run(_warm(strategies, first, last, parallel))
    except (EmporosError, ValueError, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"cache warm failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"warmed {instruments} instruments, {bars} bars")


async def _warm_reference(
    first: datetime, last: datetime, timeframe: Timeframe, symbols: list[str]
) -> dict[str, int]:
    async with open_backtest_runtime(Settings.default()) as runtime:
        if runtime.cache is None:
            raise ValueError("the backtest runtime has no candle cache")
        warm = ReferenceCacheWarm(ReferenceSeriesCatalog.load(), runtime.cache)
        return await warm.warm(first.date(), last.date(), timeframe, symbols)


_REFERENCE_TIMEFRAME = typer.Option("5m", "--timeframe", "-t", help="The stored timeframe.")
_REFERENCE_SERIES = typer.Option(
    None, "--series", help="A declared series' symbol (default: every declared series)."
)


@cache_app.command("warm-reference")
def cache_warm_reference(
    first: datetime = _FIRST,
    last: datetime = _LAST,
    timeframe: str = _REFERENCE_TIMEFRAME,
    series: list[str] = _REFERENCE_SERIES,
) -> None:
    """Read the declared index and INDIA VIX series for FROM..TO into the cache (EM-191 D2)."""
    try:
        found = asyncio.run(_warm_reference(first, last, Timeframe(timeframe), series or []))
    except (EmporosError, ValueError, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"cache warm-reference failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for series_id, bars in found.items():
        typer.echo(f"  {series_id}: {bars} bars")
    typer.echo(f"warmed {len(found)} series, {sum(found.values())} bars")
