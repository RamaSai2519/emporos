"""`emporos backtest verdicts` — what each strategy's curation concluded, recorded and readable.

A curation records its own verdicts (`backtest curate`, unless `--no-record`); a verdict reached
before verdicts were recorded is recorded from its own JSON report, never retyped. The verdict is
bound to the strategy's config file as it is today (its behaviour hash), so editing the file makes
the verdict stale until the strategy is curated again.

One thing a verdict does not say by itself: a curation sizes positions and platform limits to the
benchmark capital, so the config that was JUDGED is the file's config with its position value
rescaled. The recorded verdict carries a note when the two differ.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import typer

from emporos.backtest.curation import CurationRecord
from emporos.backtest.robustness.benchmark import (
    DEFAULT_BENCHMARK_FILE,
    BenchmarkLoader,
    BenchmarkScaler,
)
from emporos.backtest.verdict_records import CurationVerdicts, ReportVerdicts, VerdictContext
from emporos.cli.strategy_composition import build_registry
from emporos.core.clock import Clock, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.core.ids import IdGenerator
from emporos.domain.instruments import InstrumentResolver
from emporos.domain.verdicts import RecordedVerdict, standing_of
from emporos.instruments.cache import InstrumentCache
from emporos.instruments.store import InstrumentRecordMapper
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.repositories import InstrumentRepository
from emporos.persistence.verdict_store import MongoVerdictBook
from emporos.session.strategy_files import StrategyConfigLoader
from emporos.strategies.resolution import StrategyConfigResolver
from emporos.strategies.snapshot import ConfigSnapshotter

verdicts_app = typer.Typer(
    help="Recorded backtest verdicts: what each strategy's curation concluded.",
    no_args_is_help=True,
)

STRATEGY_DIR = Path("config/strategies")


class VerdictWriter(Protocol):
    async def append(self, verdict: RecordedVerdict, verdict_id: str) -> None: ...

    async def latest(self, strategy: str) -> RecordedVerdict | None: ...


class VerdictSubjects:
    """The config a verdict is about: its behaviour hash, and what differs from what was judged."""

    def __init__(self, benchmark: BenchmarkScaler, resolver: InstrumentResolver) -> None:
        self._scaler = benchmark
        self._loader = StrategyConfigLoader(StrategyConfigResolver(build_registry(), resolver))

    def hash_and_notes(self, config_file: Path) -> tuple[str, tuple[str, ...]]:
        config = self._loader.load_file(config_file)
        judged = self._scaler.strategy(config).risk.max_position_value
        notes: tuple[str, ...] = ()
        if judged != config.risk.max_position_value:
            notes = (
                f"Judged with each position sized to ₹{_plain(judged)} (the run's position "
                f"value: the benchmark's share of its capital unless declared) and the platform's "
                f"limits scaled to match; this config sizes each "
                f"position at ₹{_plain(config.risk.max_position_value)} under the real limits.",
            )
        return ConfigSnapshotter().take(config).behaviour_hash, notes


class VerdictRecorder:
    """Appends verdicts, skipping one identical to the latest, so a re-import adds nothing."""

    def __init__(self, book: VerdictWriter, ids: IdGenerator) -> None:
        self._book = book
        self._ids = ids

    async def record(self, verdicts: Sequence[RecordedVerdict]) -> list[RecordedVerdict]:
        written: list[RecordedVerdict] = []
        for verdict in verdicts:
            latest = await self._book.latest(verdict.strategy)
            if latest is not None and _same_finding(latest, verdict):
                continue
            await self._book.append(verdict, self._ids.new_ulid())
            written.append(verdict)
        return written


def _same_finding(a: RecordedVerdict, b: RecordedVerdict) -> bool:
    return (a.behaviour_hash, a.verdict, a.gates, a.experiment) == (
        b.behaviour_hash,
        b.verdict,
        b.gates,
        b.experiment,
    )


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


async def current_instruments(database: Any) -> InstrumentCache:
    """The instruments as they are now: how the worker resolves a config's universe."""
    mapper = InstrumentRecordMapper()
    records = await InstrumentRepository(database).all()
    return InstrumentCache(mapper.to_domain(r) for r in records)


async def record_curation(
    database: Any,
    records: Sequence[CurationRecord],
    config_files: dict[str, Path],
    benchmark_file: Path,
    first_day: str,
    last_day: str,
    experiment: str,
    clock: Clock | None = None,
    position_value: Decimal | None = None,
) -> list[RecordedVerdict]:
    """Record what a curation just concluded, bound to each strategy's config as it stands now.
    `position_value` is the size the curation was judged at (default: the benchmark's share)."""
    benchmark = BenchmarkLoader(benchmark_file).load()
    subjects = VerdictSubjects(
        BenchmarkScaler(benchmark, position_value), await current_instruments(database)
    )
    now = (clock or SystemClock()).now()
    verdicts = []
    for record in records:
        behaviour_hash, notes = subjects.hash_and_notes(config_files[record.strategy])
        context = VerdictContext(
            behaviour_hash, str(benchmark.capital), first_day, last_day, experiment, now, notes
        )
        verdicts.append(CurationVerdicts().of(record, context))
    return await VerdictRecorder(MongoVerdictBook(database), IdGenerator()).record(verdicts)


_REPORTS = typer.Argument(..., exists=True, dir_okay=False, help="Curation JSON report(s).")
_FROM = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="First day of the history judged.")
_TO = typer.Option(..., "--to", formats=["%Y-%m-%d"], help="Last day of the history judged.")
_EXPERIMENT = typer.Option(
    ..., help="A label for the curation these reports came from (for example em118)."
)
_BENCHMARK = typer.Option(DEFAULT_BENCHMARK_FILE, exists=True, dir_okay=False)


async def _import(
    reports: Sequence[Path], first: datetime, last: datetime, experiment: str, benchmark_file: Path
) -> list[RecordedVerdict]:
    mongo = MongoClientFactory(Settings.default())
    try:
        database = mongo.database()
        benchmark = BenchmarkLoader(benchmark_file).load()
        subjects = VerdictSubjects(BenchmarkScaler(benchmark), await current_instruments(database))
        now = SystemClock().now()
        verdicts: list[RecordedVerdict] = []
        for report in reports:
            for document in json.loads(report.read_text(encoding="utf-8")):
                file = STRATEGY_DIR / f"{document['strategy']}.yaml"
                behaviour_hash, notes = subjects.hash_and_notes(file)
                context = VerdictContext(
                    behaviour_hash,
                    str(benchmark.capital),
                    first.date().isoformat(),
                    last.date().isoformat(),
                    experiment,
                    now,
                    notes,
                )
                verdicts.append(ReportVerdicts().of(document, context))
        return await VerdictRecorder(MongoVerdictBook(database), IdGenerator()).record(verdicts)
    finally:
        await mongo.close()


@verdicts_app.command("import")
def verdicts_import(
    reports: list[Path] = _REPORTS,
    first: datetime = _FROM,
    last: datetime = _TO,
    experiment: str = _EXPERIMENT,
    benchmark: Path = _BENCHMARK,
) -> None:
    """Record verdicts from curation JSON reports, bound to each strategy's current config file.

    Use it for a curation that ran before verdicts were recorded. It assumes the config file has
    not changed since the report was produced: check `git log` on it against the report's date.
    """
    try:
        written = asyncio.run(_import(reports, first, last, experiment, benchmark))
    except (EmporosError, ValueError, KeyError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"import failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for verdict in written:
        typer.echo(f"recorded {verdict.strategy}: {verdict.verdict.value}")
    typer.echo(f"{len(written)} verdict(s) recorded")


async def _standings() -> list[tuple[str, str, RecordedVerdict | None]]:
    mongo = MongoClientFactory(Settings.default())
    try:
        database = mongo.database()
        benchmark = BenchmarkLoader(DEFAULT_BENCHMARK_FILE).load()
        subjects = VerdictSubjects(BenchmarkScaler(benchmark), await current_instruments(database))
        book = MongoVerdictBook(database)
        rows = []
        for file in sorted(STRATEGY_DIR.glob("*.yaml")):
            behaviour_hash, _ = subjects.hash_and_notes(file)
            verdict = await book.latest(file.stem)
            rows.append((file.stem, standing_of(verdict, behaviour_hash).value, verdict))
        return rows
    finally:
        await mongo.close()


@verdicts_app.command("list")
def verdicts_list() -> None:
    """Every strategy's standing today: its latest verdict against its current config file."""
    try:
        rows = asyncio.run(_standings())
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"list failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for name, standing, verdict in rows:
        detail = (
            "never curated"
            if verdict is None
            else f"{verdict.verdict.value} on {verdict.recorded_at:%Y-%m-%d} ({verdict.source})"
        )
        typer.echo(f"{name:<24} {standing:<13} {detail}")
