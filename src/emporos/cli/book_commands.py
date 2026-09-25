"""`emporos research screen-book <slug>` — run the combined-book cell A4 (EM-233).

Loads both worlds (the D1 stocks and the three index ETFs), builds the two declared sleeves and
runs every split of the committed declaration through `BookScreenRun`: each sleeve on its share of
the Rs 1,00,000, combined at the split with a quarterly reset, judged on the amended §3.2 bar.
The arms go into `docs/research/profit/screens.jsonl` once. The window is the declaration's:
from the equity sleeve's start (2017-11-13) to the end of Discovery; the report adds the
sub-period from 2018-01-01."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from emporos.cli.etf_bars_commands import DEFAULT_REPORT
from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    DeclarationGate,
    ExperimentDeclarationLoader,
)
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.rotation_commands import BENCHMARK_LABEL as ETF_BENCHMARK_LABEL
from emporos.cli.rotation_commands import BENCHMARK_WEIGHTS, CASH_YIELD
from emporos.cli.swing_worlds import (
    CAPITAL,
    EtfWorldLoader,
    StockWorldLoader,
    delivery_schedule,
)
from emporos.core.clock import SystemClock
from emporos.core.errors import EmporosError
from emporos.research.adjustments import DEFAULT_LEDGER
from emporos.research.d1_universe import DEFAULT_MANIFEST
from emporos.research.gap_classes import GapClass
from emporos.research.swing.book_cell import SleeveBookCell, SleeveInputs
from emporos.research.swing.book_report import format_book_report
from emporos.research.swing.book_runner import BookRunner
from emporos.research.swing.book_screen import BookScreenRun
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.cells import CellEnvironment
from emporos.research.swing.ledger import (
    DEFAULT_PNL_DIR,
    DEFAULT_PROFIT_SCREENS,
    DailyPnlStore,
    JsonlSwingLedger,
)
from emporos.research.swing.rules import LossStop

SUB_PERIOD_START = date(2018, 1, 1)
DECLARED_START = date(2017, 11, 13)  # the equity sleeve's start (a4-momentum-rotation-book.yaml)
BOOK_BENCHMARK_LABEL = (
    "same-weight blend of the D1 equal-weight buy-and-hold and the 60/40 NIFTYBEES/GOLDBEES "
    "buy-and-hold, same costs, same split and reset"
)

_SLUG = typer.Argument(..., help="The declared book: a4-momentum-rotation-book")
_MANIFEST = typer.Option(DEFAULT_MANIFEST, help="The committed D1 universe manifest.")
_ADJUSTMENTS = typer.Option(DEFAULT_LEDGER, help="The corporate-action adjustment ledger.")
_ETF_REPORT = typer.Option(DEFAULT_REPORT, help="The fetch-etf-bars audit (the ETFs' ids).")
_LEDGER = typer.Option(DEFAULT_PROFIT_SCREENS, help="The append-only Track A/B screen ledger.")
_PNL = typer.Option(DEFAULT_PNL_DIR, help="Where each arm's daily P&L file is written.")
_ROOT = typer.Option(None, help="Derived-candle root (default: beside the candle cache).")
_PATHS = typer.Option(10_000, help="Bootstrap paths: the plan's number; fewer only for tests.")
_START = typer.Option(DECLARED_START.isoformat(), help="First session of the book (declared).")


def research_screen_book(
    slug: str = _SLUG,
    manifest: Path = _MANIFEST,
    adjustments: Path = _ADJUSTMENTS,
    etf_report: Path = _ETF_REPORT,
    ledger: Path = _LEDGER,
    pnl_dir: Path = _PNL,
    root: Path | None = _ROOT,
    bootstrap_paths: int = _PATHS,
    first_day: str = _START,
) -> None:
    """Screen every split of the declared combined book over Discovery."""
    try:
        start_day = date.fromisoformat(first_day)
        cell = SleeveBookCell()
        if slug != cell.slug:
            raise ValueError(f"no book cell for {slug!r} (known: {cell.slug})")
        declaration = DeclarationGate(ExperimentDeclarationLoader(), GitRepository()).load(
            DEFAULT_DECLARATIONS_DIR / f"{slug}.yaml"
        )
        stock = StockWorldLoader(manifest, adjustments, root).load()
        etf = EtfWorldLoader(etf_report, root).load()
        if start_day < etf.start_day:
            raise ValueError(f"the ETFs are judged from {etf.start_day}: {start_day} is earlier")
        schedule = delivery_schedule()
        stock_env = CellEnvironment(
            stock.built.dataset, stock.regime, LossStop(), None, stock.index
        )
        etf_env = CellEnvironment(etf.built.dataset, None, LossStop(), None, etf.index)
        names = {instrument: symbol for symbol, instrument in etf.ids.items()}
        inputs = SleeveInputs(
            {etf.ids[s]: w for s, w in BENCHMARK_WEIGHTS.items()},
            CASH_YIELD, CAPITAL, start_day, frozenset(names),
        )  # fmt: skip
        sleeves = cell.sleeves(stock_env, etf_env, inputs)
        screen = BookScreenRun(
            sleeves, schedule, CAPITAL, start_day, BlockBootstrap(paths=bootstrap_paths)
        )
        runner = BookRunner(
            screen, cell, stock_env, CAPITAL, f"{stock.label}+etf3", JsonlSwingLedger(ledger),
            DailyPnlStore(pnl_dir), SystemClock().now(), stock.built.of_class(GapClass.REAL),
            f"{BOOK_BENCHMARK_LABEL} (ETF leg: {ETF_BENCHMARK_LABEL})",
            sum(s.max_positions for s in sleeves),
        )  # fmt: skip
        typer.echo(
            f"{slug}: {len(stock.built.dataset.instrument_ids)} names + {len(etf.ids)} ETFs, "
            f"capital {CAPITAL}, book from {start_day}, delivery schedule {schedule.name} "
            f"(verified: {schedule.verified}), equity sleeve = A1b weekly/vol 15, defensive "
            f"sleeve = A5 blend K=2 (cash {CASH_YIELD:.1%} a year)"
        )
        report = runner.run(declaration.parameter_grid)
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"screen-book failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in format_book_report(report, names, SUB_PERIOD_START):
        typer.echo(line)
