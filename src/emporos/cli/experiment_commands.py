"""`emporos research experiments` — declare, publish and index research experiments (EM-188).

Offline and read-mostly: it needs no broker credentials and can place no order. A declaration is
validated here exactly as `backtest curate --declaration` will read it, so a bad one is caught
before a long run, not after."""

from __future__ import annotations

from pathlib import Path

import typer

from emporos.backtest.experiment_identity import ExperimentIdMinter
from emporos.cli.experiment_declarations import ExperimentDeclarationLoader
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.experiment_registry import DEFAULT_EXPERIMENTS_DIR, FileExperimentRegistry
from emporos.core.errors import EmporosError

research_app = typer.Typer(help="Offline research tools.", no_args_is_help=True)
experiments_app = typer.Typer(
    help="Experiment declarations, reports and the registry.", no_args_is_help=True
)
research_app.add_typer(experiments_app, name="experiments")

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
