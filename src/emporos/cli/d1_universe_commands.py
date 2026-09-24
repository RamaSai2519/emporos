"""`emporos research d1-universe` — decide, once, which D1 names a cell may screen (EM-191, EM-214).

Reads the committed D1 token table and the history audit, sets the seeded holdout aside BEFORE any
bar is read, profiles the remaining names from Discovery bars only, and writes the manifest
(`config/universe/d1/universe.yaml`). The manifest is a committed fact: re-running with the same
seed and rules over the same bars writes the same file, and a screen names it by its hash."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import typer

from emporos.cli.screen_commands import vaulted_bars
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.research.d1_universe import (
    DEFAULT_MANIFEST,
    D1Profiler,
    D1UniverseBuilder,
    LiquidityRule,
)
from emporos.research.history_audit import HistoryAudit

DEFAULT_TOKENS = Path("config/universe/d1/tokens.csv")
HOLDOUT_SEED = "emporos-d1-vault-2026-09-25"  # fixed here, in code, before the first profile
HOLDOUT_FRACTION = Decimal("0.30")

_TOKENS = typer.Option(DEFAULT_TOKENS, help="The D1 symbol-to-token table.")
_OUT = typer.Option(DEFAULT_MANIFEST, help="Where the manifest is written.")
_FORCE = typer.Option(False, "--force", help="Overwrite an existing manifest.")


def research_d1_universe(tokens: Path = _TOKENS, out: Path = _OUT, force: bool = _FORCE) -> None:
    """Pick the holdout, profile the rest from Discovery, and write the D1 universe manifest."""
    try:
        if out.exists() and not force:
            raise ValueError(f"{out} exists: the universe is committed once (use --force to redo)")
        with tokens.open(encoding="utf-8", newline="") as handle:
            candidates = [f"NSE:{row['Token']}" for row in csv.DictReader(handle)]
        audit = HistoryAudit.load()
        builder = D1UniverseBuilder(HOLDOUT_SEED, HOLDOUT_FRACTION, LiquidityRule(), D1Profiler())
        manifest = builder.build(
            candidates,
            audit.instrument_ids,
            audit.quarantined,
            vaulted_bars(Settings.default(), wide=True),
        )
        manifest.save(out)
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"d1-universe failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{len(candidates)} candidates: {len(manifest.holdout)} held out (never read), "
        f"{len(manifest.included)} included, {len(manifest.excluded)} excluded, "
        f"{len(manifest.quarantined)} quarantined days; hash {manifest.content_hash[:12]}"
    )
