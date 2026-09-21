"""`emporos history check` — audit the stored bars and write the dataset report.

Read-only. Bars are read through `CandleRepository` (hot tier + cold archive) directly, not through
the backtest cache, so the audit does not make a second copy of ten years of bars.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, time
from pathlib import Path

import typer

from emporos.backtest.universe import AsOfInstruments
from emporos.cli.backtest_runtime import InstrumentErasReader
from emporos.cli.cold_storage import cold_archive
from emporos.cli.strategy_composition import build_registry
from emporos.core.clock import IST
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Timeframe
from emporos.history.quality import DatasetAuditor, QualityReport
from emporos.history.quality_report import QualityJson, QualityMarkdown
from emporos.persistence.candle_hot import MongoCandleStore
from emporos.persistence.candles import CandleRepository
from emporos.persistence.mongo import MongoClientFactory
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.resolution import StrategyConfigResolver

_STRATEGY = typer.Argument(..., exists=True, dir_okay=False, help="Strategy YAML: its universe.")
_TIMEFRAME = typer.Option("5m", "--timeframe", "-t", help="1m, 5m or 15m.")
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="First IST day.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last IST day (inclusive).")
_REPORT = typer.Option(Path("docs/data/history-quality.md"), help="The human-readable report.")
_JSON = typer.Option(Path("docs/data/history-quality.json"), "--json", help="The full record.")

NOTES = """## What this does not establish

- **Survivorship.** The universe is the instruments listed today. Names that were delisted, merged
  or dropped from the index over these years are absent, so a backtest on this set flatters the
  past. The broker offers no point-in-time constituents; an instrument that starts late (see the
  table) was listed late, which the as-of instrument master handles for the universe a run uses.
- **Adjustments.** Prices are as traded. Each `overnight_discontinuity` above is either a
  corporate action (split, bonus) or a bad bar, and needs a person's decision before a strategy
  trusts the series across it.
"""


async def _audit(
    strategy: Path, timeframe: Timeframe, first: datetime, last: datetime
) -> QualityReport:
    settings = Settings.default()
    mongo = MongoClientFactory(settings)
    try:
        database = mongo.database()
        eras = await InstrumentErasReader(database).read()
        moment = datetime.combine(first.date(), time(0, 0), tzinfo=IST).astimezone(UTC)
        universe = AsOfInstruments(eras).as_of(moment, assume_earliest_before_history=True)
        config = StrategyConfigLoader(
            StrategyConfigResolver(build_registry(), universe.resolver)
        ).load_file(strategy)
        repository = CandleRepository(MongoCandleStore(database), cold_archive(settings))
        return await DatasetAuditor(repository).audit(
            list(config.instrument_ids), timeframe, first.date(), last.date()
        )
    finally:
        await mongo.close()


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
