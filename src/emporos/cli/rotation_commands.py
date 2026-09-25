"""`emporos research screen-rotation <slug>` — run the ETF rotation cell A5 (EM-233).

Reads the committed declaration (refusing one that was not committed first), the ETF daily bars the
`fetch-etf-bars` command stored (through the vault reader, Discovery only), the same-universe
benchmark (60/40 NIFTYBEES/GOLDBEES buy-and-hold rebalanced yearly, the same costs and days), and
runs every arm with cash accruing the declared 5.0% a year. The arms go into
`docs/research/profit/screens.jsonl` once. The report adds the sub-period from 2017-11-13 (the
momentum book's start), where the ETF bars are cleanest, for arms and benchmark alike; that
sub-period is reported, not a second look."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import typer

from emporos.cli.etf_bars_commands import DEFAULT_REPORT
from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    DeclarationGate,
    ExperimentDeclarationLoader,
)
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.swing_worlds import CAPITAL, EtfWorldLoader, delivery_schedule
from emporos.core.clock import SystemClock
from emporos.core.errors import EmporosError
from emporos.research.gap_classes import GapClass
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.cells import ROTATION_CELLS, CellEnvironment
from emporos.research.swing.costs import BENCHMARK, SwingCostModel
from emporos.research.swing.ledger import (
    DEFAULT_PNL_DIR,
    DEFAULT_PROFIT_SCREENS,
    DailyPnlStore,
    JsonlSwingLedger,
)
from emporos.research.swing.metrics import SwingMetrics
from emporos.research.swing.regime import YearlyCalendar
from emporos.research.swing.report import (
    format_report,
    instrument_share_lines,
    sub_period_lines,
)
from emporos.research.swing.rules import LossStop
from emporos.research.swing.runner import CellReport, SwingCellRunner
from emporos.research.swing.screen import SwingScreenRun, judge
from emporos.research.swing.weighted import WeightedBuyHold

CASH_YIELD = Decimal("0.05")  # declared in a5-etf-dual-momentum.yaml: a liquid-fund stand-in
BENCHMARK_WEIGHTS = {"NIFTYBEES-EQ": Decimal("0.6"), "GOLDBEES-EQ": Decimal("0.4")}
BENCHMARK_LABEL = "60/40 NIFTYBEES/GOLDBEES buy-and-hold rebalanced yearly, same costs"
SUB_PERIOD_START = date(2017, 11, 13)

_SLUG = typer.Argument(..., help="The declared rotation cell: a5-etf-dual-momentum")
_ETF_REPORT = typer.Option(DEFAULT_REPORT, help="The fetch-etf-bars audit (the ETFs' ids).")
_ADJUSTMENTS = typer.Option(None, help="Unused: ETFs carry no adjustment ledger.")
_LEDGER = typer.Option(DEFAULT_PROFIT_SCREENS, help="The append-only Track A/B screen ledger.")
_PNL = typer.Option(DEFAULT_PNL_DIR, help="Where each arm's daily P&L file is written.")
_ROOT = typer.Option(None, help="Derived-candle root (the NIFTY 50 series, for the gap rule).")
_PATHS = typer.Option(10_000, help="Bootstrap paths: the plan's number; fewer only for tests.")


def _exempt_verdicts(report: CellReport, etfs: frozenset[str]) -> list[str]:
    """The arms re-judged with the index ETFs exempt from the single-name check (§3.2, amended).
    The recorded verdicts stand; this shows whether the exemption would have changed any."""
    lines = [
        "",
        "re-judged with the ETFs exempt from the concentration check (recorded verdicts stand):",
    ]
    for arm in report.arms:
        outcome = replace(arm.outcome, stats=SwingMetrics.of(arm.outcome.arm, etfs))
        verdict = judge(outcome, arm.neighbour_share)
        lines.append(
            f"{arm.label:<40} {'PASS' if verdict.passed else 'reject'}: "
            f"{'; '.join(verdict.failed_checks) or 'passed every check'}"
        )
    return lines


def research_screen_rotation(
    slug: str = _SLUG,
    etf_report: Path = _ETF_REPORT,
    ledger: Path = _LEDGER,
    pnl_dir: Path = _PNL,
    root: Path | None = _ROOT,
    bootstrap_paths: int = _PATHS,
) -> None:
    """Screen every arm of the declared ETF rotation cell over Discovery."""
    try:
        cell = ROTATION_CELLS.get(slug)
        if cell is None:
            raise ValueError(
                f"no rotation cell for {slug!r} (known: {', '.join(sorted(ROTATION_CELLS))})"
            )
        declaration = DeclarationGate(ExperimentDeclarationLoader(), GitRepository()).load(
            DEFAULT_DECLARATIONS_DIR / f"{slug}.yaml"
        )
        world = EtfWorldLoader(etf_report, root).load()
        ids, built, index, start_day = world.ids, world.built, world.index, world.start_day
        schedule = delivery_schedule()
        benchmark = WeightedBuyHold(
            built.dataset,
            {ids[s]: w for s, w in BENCHMARK_WEIGHTS.items()},
            SwingCostModel(schedule, BENCHMARK),
            YearlyCalendar(),
            CAPITAL,
            CASH_YIELD,
            start_day,
        ).run()
        screen = SwingScreenRun(
            built.dataset, schedule, benchmark, bootstrap=BlockBootstrap(paths=bootstrap_paths)
        )
        env = CellEnvironment(built.dataset, None, LossStop(), None, index)
        runner = SwingCellRunner(
            screen, env, CAPITAL, f"etf3-{len(built.audit.findings)}gaps", JsonlSwingLedger(ledger),
            DailyPnlStore(pnl_dir), SystemClock().now(), (), BENCHMARK_LABEL, CASH_YIELD,
            start_day,
        )  # fmt: skip
        typer.echo(
            f"{slug}: {len(ids)} ETFs, {built.audit.sessions_checked} sessions, gaps >= 15%: "
            f"{len(built.of_class(GapClass.ARTIFACT))} artifacts flattened, "
            f"{len(built.of_class(GapClass.REAL))} real (traded through), capital {CAPITAL}, "
            f"cash accrues {CASH_YIELD:.1%} a year, all judged from {start_day}, delivery schedule "
            f"{schedule.name} (verified: {schedule.verified})"
        )
        report = runner.run(cell, declaration.parameter_grid)
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"screen-rotation failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in format_report(report):
        typer.echo(line)
    runs = [(arm.label, arm.outcome.arm) for arm in report.arms]
    names = {instrument: symbol for symbol, instrument in ids.items()}
    for line in instrument_share_lines(runs, names, "each ETF's share of net trade profit:"):
        typer.echo(line)
    for line in _exempt_verdicts(report, frozenset(names)):
        typer.echo(line)
    for line in sub_period_lines([*runs, ("BENCHMARK 60/40", benchmark)], SUB_PERIOD_START):
        typer.echo(line)
