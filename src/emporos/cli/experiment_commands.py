"""`emporos research experiments` — declare, publish and index research experiments (EM-188).

Offline and read-mostly: it needs no broker credentials and can place no order. A declaration is
validated here exactly as `backtest curate --declaration` will read it, so a bad one is caught
before a long run, not after."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from emporos.backtest.experiment_backfill import ReportBackfill
from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.backtest.robustness.verdict import VerdictPolicy
from emporos.cli.book_commands import research_screen_book
from emporos.cli.core_commands import research_screen_core
from emporos.cli.corporate_actions_commands import (
    research_build_adjustments,
    research_collect_actions,
)
from emporos.cli.d1_universe_commands import research_d1_universe
from emporos.cli.daily_bars_commands import research_build_daily_bars
from emporos.cli.discontinuity_commands import research_audit_discontinuities
from emporos.cli.etf_bars_commands import research_fetch_etf_bars
from emporos.cli.experiment_backfill import (
    DEFAULT_STRATEGIES_DIR,
    BackfillSources,
    BackfillSummary,
    ExperimentBackfill,
)
from emporos.cli.experiment_declarations import DeclarationGate, ExperimentDeclarationLoader
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.experiment_registry import (
    DEFAULT_EXPERIMENTS_DIR,
    ExperimentPublication,
    FileExperimentRegistry,
)
from emporos.cli.fo_archive_commands import research_build_fo_specs, research_collect_fo_archive
from emporos.cli.index_change_commands import research_build_index_changes
from emporos.cli.index_notice_commands import research_collect_index_notices
from emporos.cli.option_screen_commands import research_screen_options
from emporos.cli.ranked_screen_commands import research_screen_ranked
from emporos.cli.results_commands import research_collect_results
from emporos.cli.rotation_commands import research_screen_rotation
from emporos.cli.screen_commands import research_screen
from emporos.cli.stress_commands import research_stress_book
from emporos.cli.swing_commands import research_screen_swing
from emporos.cli.vault_commands import vault_app
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.research_experiments import (
    ExperimentDeclaration,
    ExperimentFamily,
    VersionStamp,
)
from emporos.persistence.cross_sectional_ledger import MongoCrossSectionalTrialLedger
from emporos.persistence.feature_ledger import MongoFeatureTrialLedger
from emporos.persistence.hypothesis_store import MongoHypothesisRegistry
from emporos.persistence.lead_lag_ledger import MongoLeadLagTrialLedger
from emporos.persistence.mongo import MongoClientFactory
from emporos.research.experiment_report import (
    LEDGER_FAMILIES,
    BackfilledDeclaration,
    LedgerExperiment,
    LedgerExperimentReader,
    LedgerExperimentReportBuilder,
)

research_app = typer.Typer(help="Offline research tools.", no_args_is_help=True)
experiments_app = typer.Typer(
    help="Experiment declarations, reports and the registry.", no_args_is_help=True
)
research_app.add_typer(experiments_app, name="experiments")
research_app.add_typer(vault_app, name="vault")
research_app.command("screen")(research_screen)
research_app.command("screen-ranked")(research_screen_ranked)
research_app.command("collect-results")(research_collect_results)
research_app.command("collect-fo-archive")(research_collect_fo_archive)
research_app.command("build-fo-specs")(research_build_fo_specs)
research_app.command("screen-options")(research_screen_options)
research_app.command("d1-universe")(research_d1_universe)
research_app.command("build-daily-bars")(research_build_daily_bars)
research_app.command("collect-actions")(research_collect_actions)
research_app.command("build-adjustments")(research_build_adjustments)
research_app.command("screen-swing")(research_screen_swing)
research_app.command("screen-rotation")(research_screen_rotation)
research_app.command("screen-book")(research_screen_book)
research_app.command("screen-core")(research_screen_core)
research_app.command("stress-book")(research_stress_book)
research_app.command("collect-index-notices")(research_collect_index_notices)
research_app.command("build-index-changes")(research_build_index_changes)
research_app.command("fetch-etf-bars")(research_fetch_etf_bars)
research_app.command("audit-discontinuities")(research_audit_discontinuities)

_ROOT = typer.Option(DEFAULT_EXPERIMENTS_DIR, help="Where published experiment reports live.")
_DECLARATION = typer.Argument(..., exists=True, dir_okay=False, help="An experiment declaration.")


def _fail(what: str, error: EmporosError) -> typer.Exit:
    typer.secho(f"{what} failed: {error.message}", fg=typer.colors.RED)
    return typer.Exit(code=1)


@experiments_app.command("declare")
def experiments_declare(declaration: Path = _DECLARATION) -> None:
    """Validate a declaration and print the experiment id it will publish under."""
    try:
        declared = ExperimentDeclarationLoader().load(declaration)
    except EmporosError as error:
        raise _fail("declare", error) from error
    experiment_id = ExperimentIdMinter().mint(declared)
    committed = GitRepository().is_committed(declaration)
    typer.echo(f"{experiment_id}  {declared.family.value}  {declared.slug}")
    if not committed:
        typer.secho(
            "not committed yet: commit it before the run, or the run cannot show it was declared "
            "first",
            fg=typer.colors.YELLOW,
        )


@experiments_app.command("index")
def experiments_index(root: Path = _ROOT) -> None:
    """Rebuild INDEX.md and index.json from the published reports."""
    count = FileExperimentRegistry(root).rebuild_index()
    typer.echo(f"{count} experiment(s) indexed in {root}")


_HYPOTHESIS = typer.Option(..., "--hypothesis", help="The declared hypothesis id.")
_FAMILY = typer.Option(
    ExperimentFamily.FEATURE,
    "--family",
    help="Which ledger holds its trials: feature, cross_sectional or lead_lag.",
)
_OPTIONAL_DECLARATION = typer.Option(
    None,
    "--declaration",
    exists=True,
    dir_okay=False,
    help="The experiment declaration (config/experiments/<slug>.yaml). Without one the report is "
    "built from the recorded hypothesis alone and is marked BACKFILLED_NOT_PREDECLARED.",
)
_UNCOMMITTED = typer.Option(
    False,
    "--allow-uncommitted-declaration",
    help="Publish even though the declaration is not committed.",
)


async def _read(family: ExperimentFamily, hypothesis_id: str) -> LedgerExperiment:
    mongo = MongoClientFactory(Settings.default())
    try:
        database = mongo.database()
        reader = LedgerExperimentReader(
            MongoHypothesisRegistry(database),
            MongoFeatureTrialLedger(database),
            MongoCrossSectionalTrialLedger(database),
            MongoLeadLagTrialLedger(database),
        )
        return await reader.read(family, hypothesis_id)
    finally:
        await mongo.close()


@experiments_app.command("report")
def experiments_report(
    hypothesis: str = _HYPOTHESIS,
    family: ExperimentFamily = _FAMILY,
    declaration: Path | None = _OPTIONAL_DECLARATION,
    root: Path = _ROOT,
    allow_uncommitted_declaration: bool = _UNCOMMITTED,
) -> None:
    """Publish the report of a feature, cross-sectional or lead-lag hypothesis from its ledger."""
    try:
        declared: ExperimentDeclaration | None = None
        if declaration is not None:
            gate = DeclarationGate(ExperimentDeclarationLoader(), GitRepository())
            declared = gate.load(declaration, allow_uncommitted=allow_uncommitted_declaration)
        if family not in LEDGER_FAMILIES:
            raise ValueError(f"{family.value} experiments are not backed by a trial ledger")
        source = asyncio.run(_read(family, hypothesis))
        publication = ExperimentPublication(
            LedgerExperimentReportBuilder(), FileExperimentRegistry(root)
        )
        report, outcome = publication.publish(
            source,
            declared or BackfilledDeclaration.of(family, source.hypothesis),
            VersionStamp(code_revision=GitRepository().revision()),
        )
    except (EmporosError, ValueError, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"report failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{report.experiment_id}: {report.outcome.value.upper()} ({outcome.value}) in {root}"
    )


_STRATEGIES_DIR = typer.Option(
    DEFAULT_STRATEGIES_DIR, help="The published curation reports to backfill."
)
_LEDGERS = typer.Option(
    False,
    "--ledgers/--no-ledgers",
    help="Also backfill every hypothesis in the feature, cross-sectional and lead-lag ledgers "
    "(reads each ledger once from the shared database).",
)


async def _read_all() -> list[LedgerExperiment]:
    mongo = MongoClientFactory(Settings.default())
    try:
        database = mongo.database()
        return await LedgerExperimentReader(
            MongoHypothesisRegistry(database),
            MongoFeatureTrialLedger(database),
            MongoCrossSectionalTrialLedger(database),
            MongoLeadLagTrialLedger(database),
        ).read_all()
    finally:
        await mongo.close()


def _echo(what: str, summary: BackfillSummary) -> None:
    typer.echo(
        f"{what}: {summary.written} written, {summary.unchanged} already published "
        f"({summary.indexed} experiment(s) in the index)"
    )


@experiments_app.command("backfill")
def experiments_backfill(
    strategies_dir: Path = _STRATEGIES_DIR, root: Path = _ROOT, ledgers: bool = _LEDGERS
) -> None:
    """Publish the reports that predate the registry, marked BACKFILLED_NOT_PREDECLARED.

    Nothing is invented: what a source never recorded is n/a and no outcome is upgraded. Safe to
    re-run: a report already published is left as it is."""
    try:
        codes = {
            gate.name: gate.code
            for gate in VerdictPolicy.standard(BenchmarkLoader().load().verdict).gates
        }
        backfill = ExperimentBackfill(FileExperimentRegistry(root), ReportBackfill(codes))
        sources = BackfillSources(GitRepository()).discover(strategies_dir)
        _echo("curation reports", backfill.curation_reports(sources))
        if ledgers:
            _echo("ledger hypotheses", backfill.ledger_reports(asyncio.run(_read_all())))
    except (EmporosError, ValueError, LookupError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"backfill failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
