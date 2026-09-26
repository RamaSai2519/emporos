"""`emporos research backup-datasets`: copy the research datasets to our own S3 bucket (EM-244).

Upload only (`aws s3 sync` without `--delete`), from the research dir (`EMPOROS_RESEARCH_DIR`) to
`s3://<bucket>/research-backup/<dataset>/`, using the machine's default AWS profile."""

from __future__ import annotations

from pathlib import Path

import typer

from emporos.core.paths import research_dir
from emporos.research.backup import DEFAULT_BUCKET, DatasetBackup, SubprocessRunner

_DATASETS = typer.Argument(None, help="Dataset directories to back up (default: all of them).")
_BUCKET = typer.Option(DEFAULT_BUCKET, help="The destination s3:// prefix.")
_ROOT = typer.Option(None, help="The research dir (default: EMPOROS_RESEARCH_DIR or the default).")
_DRY = typer.Option(False, "--dry-run", help="Show what would be copied, copy nothing.")


def research_backup_datasets(
    datasets: list[str] | None = _DATASETS,
    bucket: str = _BUCKET,
    root: Path | None = _ROOT,
    dry_run: bool = _DRY,
) -> None:
    """Sync each dataset under the research dir to the backup bucket (never deletes remotely)."""
    try:
        backup = DatasetBackup(root or research_dir(), bucket, SubprocessRunner())
        results = backup.run(datasets or (), dry_run)
    except (ValueError, OSError) as error:
        typer.secho(f"backup-datasets failed: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for result in results:
        typer.echo(f"{result.dataset}: {'ok' if result.ok else f'FAILED ({result.exit_code})'}")
    if not all(r.ok for r in results):
        raise typer.Exit(code=2)
