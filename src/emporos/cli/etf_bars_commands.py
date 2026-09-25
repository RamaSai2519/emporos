"""`emporos research fetch-etf-bars`: daily bars for NIFTYBEES, JUNIORBEES, GOLDBEES (EM-233).

Resolves the three ETFs from the broker's public scrip master (no Atlas), logs in ONCE to the
broker's history API from this machine, fetches `ONE_DAY` bars in 365-day windows, stores them in
the cold tier and audits what it stored: first and last day, bars per year, sessions missing
(against the NIFTY 50 series where it exists) and every >= 15% open gap, classified by the same
real-or-artifact rule as the equity names (a NIFTYBEES or GOLDBEES unit split shows up as a
split-shaped artifact). It reads no order, places none, and stops the whole run on the first
broker error. Development machine only; one session per client code, so the worker must not be
running."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml

from emporos.broker.angelone.api import AngelOneApi
from emporos.broker.angelone.candle_backfill import AngelOneCandleBackfill, BarRejections
from emporos.broker.angelone.factory import AngelOneStackFactory
from emporos.broker.backoff import RandomJitter
from emporos.cli.cold_storage import DEFAULT_COLD_DIR, cold_archive
from emporos.cli.daily_bars_commands import derived_candle_root
from emporos.cli.swing_worlds import NIFTY_50
from emporos.core.clock import IST, AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Timeframe
from emporos.instruments.downloader import MASTER_URL
from emporos.persistence.candle_cache import CandleCacheFiles, ColdArchiveFiles, FileCandleReader
from emporos.research.etf_bars import EtfBarAudit, EtfBarFetcher, EtfFetchReport, EtfResolver
from emporos.research.swing.regime import IndexSeries

DEFAULT_REPORT = Path("docs/research/profit/etf-bars-report.yaml")
_FROM = typer.Option(datetime(2001, 1, 1), "--from", formats=["%Y-%m-%d"], help="First day.")
_TO = typer.Option(datetime(2026, 3, 18), "--to", formats=["%Y-%m-%d"], help="Last day (in).")
_REPORT = typer.Option(DEFAULT_REPORT, help="Where the audit of the stored bars is written.")


async def _master() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(MASTER_URL)
        response.raise_for_status()
        rows: list[dict[str, Any]] = response.json()
        return rows


async def _fetch(settings: Settings, first: date, last: date) -> tuple[list[EtfFetchReport], int]:
    instruments = EtfResolver().resolve(await _master())
    broker = AngelOneStackFactory(settings, SystemClock(), AsyncioSleeper(), RandomJitter()).build()
    rejected = BarRejections()
    try:
        source = AngelOneCandleBackfill(AngelOneApi(broker.transport), rejected)
        reports = await EtfBarFetcher(source, cold_archive(settings)).run(instruments, first, last)
    finally:
        try:
            await broker.sessions.logout()
        finally:
            await broker.aclose()
    return reports, len(rejected.bars)


def research_fetch_etf_bars(
    first: datetime = _FROM, last: datetime = _TO, report: Path = _REPORT
) -> None:
    """Fetch the ETFs' daily history from the broker into the cold tier and audit it."""
    try:
        settings = Settings.default()
        reports, rejected = asyncio.run(_fetch(settings, first.date(), last.date()))
        document = _audit(settings, reports, rejected, first.date(), last.date())
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(yaml.safe_dump(document, sort_keys=False, width=140), encoding="utf-8")
    except (EmporosError, ValueError, OSError, httpx.HTTPError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"fetch-etf-bars failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for row in document["etfs"]:
        typer.echo(
            f"{row['symbol']}: {row['bars']} bars {row['first_day']}..{row['last_day']}, "
            f"{len(row['sparse_windows'])} sparse window(s), {len(row['gaps_against_nifty'])} "
            f"session(s) missing since the index series starts, gaps by class {row['gap_classes']}"
        )
    typer.echo(f"{rejected} invalid bar(s) skipped; audit written to {report}")


def _audit(
    settings: Settings, reports: list[EtfFetchReport], rejected: int, first: date, last: date
) -> dict[str, Any]:
    cold = FileCandleReader(
        [ColdArchiveFiles(Path(settings.cold_archive_dir or DEFAULT_COLD_DIR))], memoize=False
    )
    derived = FileCandleReader([CandleCacheFiles(derived_candle_root(settings))], memoize=False)
    start = datetime.combine(first, time(0, 0), tzinfo=IST)
    end = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=IST)
    nifty = asyncio.run(derived.get_range(NIFTY_50, Timeframe.D1, start, end))
    index = IndexSeries(nifty)
    sessions = [b.ts.astimezone(IST).date() for b in nifty]
    auditor = EtfBarAudit(index, sessions)
    etfs = [
        auditor.audit(r, asyncio.run(cold.get_range(r.instrument_id, Timeframe.D1, start, end)))
        for r in reports
    ]
    return {
        "fetched_on": SystemClock().now().date().isoformat(),
        "rejected_bars": rejected,
        "etfs": etfs,
    }
