"""`emporos history check` — audit the stored bars and write the dataset report.

Read-only. Bars are read through `CachingCandleReader` (backed by `CandleRepository`: hot tier +
cold archive), the same local read-through Parquet cache backtests use — a multi-year audit over
the whole universe is exactly the "same bars read again and again" case that cache exists for, and
the shared Atlas cluster has real transfer limits (EM-177). A closed month is read from Mongo/S3
ONCE per machine and served from `~/.cache/emporos/candles` on every run after.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

import typer
from pymongo.asynchronous.database import AsyncDatabase

from emporos.backtest.universe import AsOfInstruments
from emporos.cli.backtest_runtime import InstrumentErasReader, candle_cache_root
from emporos.cli.cold_storage import cold_archive
from emporos.cli.strategy_composition import build_registry
from emporos.core.clock import IST, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Timeframe
from emporos.history.quality import DatasetAuditor, OvernightDiscontinuity, QualityReport
from emporos.history.quality_report import QualityJson, QualityMarkdown
from emporos.history.quarantine import CorporateActionQuarantine, QuarantineCurator
from emporos.persistence.candle_cache import CachingCandleReader, CandleCacheFiles
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleRepository
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.quarantine_store import MongoQuarantineStore
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigResolver

Database = AsyncDatabase[Mapping[str, Any]]

_STRATEGY = typer.Argument(..., exists=True, dir_okay=False, help="Strategy YAML: its universe.")
_TIMEFRAME = typer.Option("5m", "--timeframe", "-t", help="1m, 5m or 15m.")
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="First IST day.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last IST day (inclusive).")
_REPORT = typer.Option(Path("docs/data/history-quality.md"), help="The human-readable report.")
_JSON = typer.Option(Path("docs/data/history-quality.json"), "--json", help="The full record.")
_WRITE = typer.Option(False, "--write", help="Persist proposed entries (default: preview only).")

NOTES = """## What this does not establish

- **Survivorship.** The universe is the instruments listed today. Names that were delisted, merged
  or dropped from the index over these years are absent, so a backtest on this set flatters the
  past. The broker offers no point-in-time constituents; an instrument that starts late (see the
  table) was listed late, which the as-of instrument master handles for the universe a run uses.
- **Adjustments.** Prices are as traded. Each `overnight_discontinuity` above is either a
  corporate action (split, bonus) or a bad bar, and needs a person's decision before a strategy
  trusts the series across it. Run `emporos history quarantine` to propose/persist a `DETECTED`
  quarantine entry for each one, so no backtest trades across it unadjusted in the meantime.

Full list, with every other known limitation and how each is versioned per run:
`docs/data/known-limitations.md`.
"""


def _cached_reader(database: Database, settings: Settings) -> CachingCandleReader:
    repository = CandleRepository(MongoCandleStore(database), cold_archive(settings))
    cache = CandleCacheFiles(candle_cache_root(settings))
    return CachingCandleReader(repository, cache, SystemClock())


async def _config_for(
    strategy: Path, database: Database, first: datetime
) -> ResolvedStrategyConfig:
    eras = await InstrumentErasReader(database).read()
    moment = datetime.combine(first.date(), time(0, 0), tzinfo=IST).astimezone(UTC)
    universe = AsOfInstruments(eras).as_of(moment, assume_earliest_before_history=True)
    return StrategyConfigLoader(
        StrategyConfigResolver(build_registry(), universe.resolver)
    ).load_file(strategy)


async def _audit(
    strategy: Path, timeframe: Timeframe, first: datetime, last: datetime
) -> QualityReport:
    settings = Settings.default()
    mongo = MongoClientFactory(settings)
    try:
        database = mongo.database()
        config = await _config_for(strategy, database, first)
        return await DatasetAuditor(_cached_reader(database, settings)).audit(
            list(config.instrument_ids), timeframe, first.date(), last.date()
        )
    finally:
        await mongo.close()


async def _quarantine(
    strategy: Path, timeframe: Timeframe, first: datetime, last: datetime, write: bool
) -> tuple[QualityReport, int]:
    settings = Settings.default()
    mongo = MongoClientFactory(settings)
    try:
        database = mongo.database()
        config = await _config_for(strategy, database, first)
        report = await DatasetAuditor(_cached_reader(database, settings)).audit(
            list(config.instrument_ids), timeframe, first.date(), last.date()
        )
        store = MongoQuarantineStore(database)
        existing = CorporateActionQuarantine(await store.load_all())
        findings = [
            (f.instrument_id, f.day, f.detail)
            for f in report.findings
            if f.check == OvernightDiscontinuity.name and f.day is not None
        ]
        proposed = QuarantineCurator().propose(findings, existing, SystemClock().now())
        if write and proposed:
            await store.add(proposed)
        return report, len(proposed)
    finally:
        await mongo.close()


def _quarantine_message(hits: int, proposed: int, write: bool) -> list[str]:
    lines = [f"{hits} overnight_discontinuity finding(s), {proposed} newly proposed"]
    if not write and proposed:
        lines.append("nothing written: re-run with --write once these are reviewed")
    return lines


def history_quarantine(
    strategy: Path = _STRATEGY,
    timeframe: str = _TIMEFRAME,
    first: datetime = _FROM,
    last: datetime = _TO,
    write: bool = _WRITE,
) -> None:
    """Propose (or, with `--write`, persist) DETECTED quarantine entries from this run's
    `overnight_discontinuity` findings — every one still needs a person's review (EM-177)."""
    try:
        report, proposed = asyncio.run(
            _quarantine(strategy, Timeframe(timeframe), first, last, write)
        )
    except (EmporosError, ValueError, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"quarantine failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in _quarantine_message(report.count(OvernightDiscontinuity.name), proposed, write):
        typer.echo(line)


def history_check(
    strategy: Path = _STRATEGY,
    timeframe: str = _TIMEFRAME,
    first: datetime = _FROM,
    last: datetime = _TO,
    report: Path = _REPORT,
    record: Path = _JSON,
) -> None:
    """Check duplicates, timestamps, gaps and split/bonus jumps over the stored bars."""
    try:
        result = asyncio.run(_audit(strategy, Timeframe(timeframe), first, last))
    except (EmporosError, ValueError, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"check failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(QualityMarkdown().render(result, NOTES), encoding="utf-8")
    record.write_text(json.dumps(QualityJson().of(result), indent=1) + "\n", encoding="utf-8")
    typer.echo(
        f"{len(result.spans)} instruments, {result.market_days} market days, "
        f"{len(result.findings)} findings ({result.errors} errors)\n"
        f"report: {report}\nrecord: {record}"
    )
    if result.errors:
        raise typer.Exit(code=2)
