"""`emporos research screen-swing <slug>` — run a declared Track A cell (EM-228, EM-229).

Reads the committed declaration (refusing one that was not committed first), the D1 universe, the
derived daily bars (through the vault reader, Discovery only), the adjustment ledger, the NIFTY 50
regime series and, for A2, the results events. It refuses to run at all while the adjustment ledger
is empty: PROFIT_PLAN §2.6 puts corporate actions before any Track A screen. Every arm goes into
`docs/research/profit/screens.jsonl` once, with its daily P&L file."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import typer

from emporos.cli.corporate_actions_commands import research_symbols
from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    DeclarationGate,
    ExperimentDeclarationLoader,
)
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.swing_worlds import CAPITAL, StockWorldLoader, delivery_schedule
from emporos.core.clock import SystemClock
from emporos.core.errors import EmporosError
from emporos.research.adjustments import DEFAULT_LEDGER
from emporos.research.d1_universe import DEFAULT_MANIFEST
from emporos.research.gap_classes import GapClass
from emporos.research.results_filings import FilingLedger
from emporos.research.scans.event_days import results_by_symbol
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.cells import CELLS, CellEnvironment
from emporos.research.swing.earnings_drift import ReactionSessions
from emporos.research.swing.ledger import (
    DEFAULT_PNL_DIR,
    DEFAULT_PROFIT_SCREENS,
    DailyPnlStore,
    JsonlSwingLedger,
)
from emporos.research.swing.report import format_report
from emporos.research.swing.rules import LossStop
from emporos.research.swing.runner import SwingCellRunner
from emporos.research.swing.screen import SwingScreenRun

DEFAULT_EVENTS_DIR = Path("docs/research/edge-search/events")
DEFAULT_TOKENS = Path("config/universe/d1/tokens.csv")

_SLUG = typer.Argument(..., help="The declared cell: a1-momentum-trend-filter or a2-...")
_MANIFEST = typer.Option(DEFAULT_MANIFEST, help="The committed D1 universe manifest.")
_TOKENS = typer.Option(DEFAULT_TOKENS, help="The D1 symbol-to-token table.")
_ADJUSTMENTS = typer.Option(DEFAULT_LEDGER, help="The corporate-action adjustment ledger.")
_LEDGER = typer.Option(DEFAULT_PROFIT_SCREENS, help="The append-only Track A/B screen ledger.")
_PNL = typer.Option(DEFAULT_PNL_DIR, help="Where each arm's daily P&L file is written.")
_EVENTS = typer.Option(DEFAULT_EVENTS_DIR, help="The results-events ledger directory.")
_ROOT = typer.Option(None, help="Derived-candle root (default: beside the candle cache).")
_PATHS = typer.Option(10_000, help="Bootstrap paths: the plan's number; fewer only for tests.")


def _published_by_instrument(
    events: Path, symbols: dict[str, str], instruments: set[str]
) -> dict[str, list[datetime]]:
    ledger = FilingLedger(events / "results-filings.jsonl", events / "results-collected.jsonl")
    by_symbol = results_by_symbol(ledger.load())
    return {
        symbols[s]: moments for s, moments in by_symbol.items() if symbols.get(s) in instruments
    }


def research_screen_swing(
    slug: str = _SLUG,
    manifest: Path = _MANIFEST,
    tokens: Path = _TOKENS,
    adjustments: Path = _ADJUSTMENTS,
    ledger: Path = _LEDGER,
    pnl_dir: Path = _PNL,
    events: Path = _EVENTS,
    root: Path | None = _ROOT,
    bootstrap_paths: int = _PATHS,
) -> None:
    """Screen every arm of a declared Track A cell over Discovery."""
    try:
        cell = CELLS.get(slug)
        if cell is None:
            raise ValueError(f"no swing cell for {slug!r} (known: {', '.join(sorted(CELLS))})")
        declaration = DeclarationGate(ExperimentDeclarationLoader(), GitRepository()).load(
            DEFAULT_DECLARATIONS_DIR / f"{slug}.yaml"
        )
        world = StockWorldLoader(manifest, adjustments, root).load()
        universe, factors, built, index = world.universe, world.factors, world.built, world.index
        symbols = research_symbols(manifest, tokens)
        by_instrument = _published_by_instrument(events, symbols, set(universe.instrument_ids))
        sessions: dict[str, list[date]] = defaultdict(list)
        for name in built.dataset.instrument_ids:
            sessions[name] = list(built.dataset.series(name).days)
        env = CellEnvironment(
            built.dataset,
            world.regime,
            LossStop(),
            ReactionSessions.build(by_instrument, sessions),
            index,
        )
        schedule = delivery_schedule()
        screen = SwingScreenRun(
            built.dataset,
            schedule,
            SwingScreenRun.universe(built.dataset, schedule),
            bootstrap=BlockBootstrap(paths=bootstrap_paths),
        )
        runner = SwingCellRunner(
            screen, env, CAPITAL, world.label, JsonlSwingLedger(ledger), DailyPnlStore(pnl_dir),
            SystemClock().now(), built.of_class(GapClass.REAL),
        )  # fmt: skip
        typer.echo(
            f"{slug}: {len(built.dataset.instrument_ids)} names, {built.audit.sessions_checked} "
            f"sessions, gaps >= 15%: {len(built.of_class(GapClass.EXPLAINED))} explained, "
            f"{len(built.of_class(GapClass.ARTIFACT))} artifacts flattened, "
            f"{len(built.of_class(GapClass.REAL))} real (traded through), adjustment ledger "
            f"{len(factors)} factors, capital {CAPITAL}, delivery schedule {schedule.name} "
            f"(verified: {schedule.verified})"
        )
        report = runner.run(cell, declaration.parameter_grid)
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"screen-swing failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in format_report(report):
        typer.echo(line)
