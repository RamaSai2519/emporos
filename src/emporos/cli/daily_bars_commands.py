"""`emporos research build-daily-bars`: daily bars for the D1 names (PROFIT_PLAN A-F1, EM-220).

Builds each `included` name's session bars from the local 5m files (the cache, then the cold
archive; no database, no network) through the vault reader, for Discovery and Confirmation, and
writes them under a DERIVED root beside the candle cache, never into it: the cache's own 1d files
are the broker's daily bars for the audited names, including days past the vault's first, and a
derived month must not replace one. The holdout is not in `included`, so it is never read."""

from __future__ import annotations

from pathlib import Path

import typer

from emporos.cli.backtest_runtime import candle_cache_root
from emporos.cli.screen_commands import vaulted_bars
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.persistence.candle_cache import CandleCacheFiles
from emporos.research.d1_universe import DEFAULT_MANIFEST, D1Manifest
from emporos.research.daily_bars import DailyBarBuilder, DailyBarStore
from emporos.research.partition import CONFIRMATION, DISCOVERY

DERIVED_DIR_NAME = "candles-derived"

_MANIFEST = typer.Option(DEFAULT_MANIFEST, help="The committed D1 universe manifest.")
_OUT = typer.Option(None, help="Derived-candle root (default: beside the candle cache).")
_SHARD = typer.Option(
    "1/1",
    help="Build only the names whose position is N mod M, as 'N/M' (N from 1): run M at once.",
)


def derived_candle_root(settings: Settings) -> Path:
    return candle_cache_root(settings).parent / DERIVED_DIR_NAME


def parse_shard(text: str) -> tuple[int, int]:
    """'N/M' to (N-1, M): which residue class of the name list this process builds."""
    try:
        number, total = (int(part) for part in text.split("/"))
    except ValueError:
        raise ValueError(f"a shard is 'N/M', got {text!r}") from None
    if not 1 <= number <= total:
        raise ValueError(f"shard {text!r}: N is from 1 to M")
    return number - 1, total


def research_build_daily_bars(
    manifest: Path = _MANIFEST, out: Path | None = _OUT, shard: str = _SHARD
) -> None:
    """Session OHLCV bars for the D1 names from their 5m bars, Discovery and Confirmation."""
    try:
        settings = Settings.default()
        universe = D1Manifest.load(manifest)
        root = out or derived_candle_root(settings)
        builder = DailyBarBuilder(
            vaulted_bars(settings, wide=True),
            DailyBarStore(CandleCacheFiles(root)),
            (DISCOVERY, CONFIRMATION),
        )
        residue, total = parse_shard(shard)
        reports = []
        for instrument_id in universe.included[residue::total]:
            report = builder.build(instrument_id)
            reports.append(report)
            typer.echo(
                f"{instrument_id}: {report.sessions} sessions {report.first}..{report.last}, "
                f"{report.partial_days} partial, {report.thin_edge_days} thin-edge, "
                f"{report.off_hours_dropped} off-hours bars dropped"
            )
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"build-daily-bars failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{len(reports)} names, {sum(r.sessions for r in reports)} daily bars, "
        f"{sum(r.partial_days for r in reports)} partial, "
        f"{sum(r.thin_edge_days for r in reports)} thin-edge, written under {root}"
    )
