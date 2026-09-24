"""`emporos backtest trials` — the experiment ledger: what has been tried, and how often."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import typer

from emporos.backtest.robustness.backfill import PastExperiments
from emporos.backtest.robustness.program_trials import ProgramTrials
from emporos.backtest.robustness.trials import TrialLedger, TrialStatistics
from emporos.cli.program_trials import ProgramTrialCountFactory
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.experiments import DuplicateTrialError, Trial
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.trial_ledger import MongoTrialLedger

T = TypeVar("T")

trials_app = typer.Typer(
    help="The append-only ledger of every experiment tried.", no_args_is_help=True
)

_CURATION = typer.Option(
    [Path(f"docs/strategies/{n}.json") for n in ("orb_v1", "vwap_reversion_v1", "rsi_pullback_v1")],
    help="Published curation records to backfill (repeatable).",
)
_MOMENTUM = typer.Option(
    Path("docs/backtests/momentum_v1_2025-09-22_2026-09-18.json"),
    help="The published momentum_v1 report to backfill.",
)


async def _with_ledger(work: Callable[[TrialLedger], Awaitable[T]]) -> T:
    mongo = MongoClientFactory(Settings.default())
    try:
        return await work(MongoTrialLedger(mongo.database()))
    finally:
        await mongo.close()


def _summary(trials: list[Trial]) -> list[str]:
    stats = TrialStatistics.of(trials)
    lines = [
        f"{stats.count} trials, {stats.scored} carry a Sharpe ratio"
        + ("" if stats.sharpe_variance is None else f" (variance {stats.sharpe_variance:.6f})")
    ]
    by_strategy = Counter((t.experiment, t.strategy) for t in trials)
    lines += [f"  {exp} / {name}: {n}" for (exp, name), n in sorted(by_strategy.items())]
    return lines


@trials_app.command("list")
def trials_list() -> None:
    """Count what has been tried, by experiment and strategy."""
    try:
        trials = asyncio.run(_with_ledger(lambda ledger: ledger.all()))
    except EmporosError as error:
        typer.secho(f"trial ledger unavailable: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in _summary(trials):
        typer.echo(line)


async def _append_missing(ledger: TrialLedger, trials: list[Trial]) -> tuple[int, int]:
    added = skipped = 0
    for trial in trials:
        try:
            await ledger.append(trial)
            added += 1
        except DuplicateTrialError:
            skipped += 1
    return added, skipped


@trials_app.command("backfill")
def trials_backfill(curation: list[Path] = _CURATION, momentum: Path = _MOMENTUM) -> None:
    """Record the experiments run before the ledger existed, from their published records."""
    past = PastExperiments(datetime.now(UTC))
    records = [record for path in curation for record in json.loads(path.read_text("utf-8"))]
    trials = [*past.curation(records), *past.momentum(json.loads(momentum.read_text("utf-8")))]
    try:
        added, skipped = asyncio.run(_with_ledger(lambda ledger: _append_missing(ledger, trials)))
    except EmporosError as error:
        typer.secho(f"trial ledger unavailable: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"backfill: {added} trials added, {skipped} already recorded")


def _program_summary(program: ProgramTrials) -> list[str]:
    stats = program.statistics
    lines = [f"program-wide N = {stats.count} ({stats.scored} carry a Sharpe ratio)"]
    lines += [f"  {name}: {count}" for name, count in program.by_source.items()]
    return lines


async def _program_trials() -> ProgramTrials:
    mongo = MongoClientFactory(Settings.default())
    try:
        return await ProgramTrialCountFactory().build(mongo.database()).trials()
    finally:
        await mongo.close()


@trials_app.command("program")
def trials_program() -> None:
    """Program-wide N (EM-191): every look in every ledger plus every published experiment."""
    try:
        program = asyncio.run(_program_trials())
    except EmporosError as error:
        typer.secho(f"trial ledgers unavailable: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in _program_summary(program):
        typer.echo(line)
