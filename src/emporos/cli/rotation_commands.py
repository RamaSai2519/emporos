"""`emporos research screen-rotation <slug>` — run the ETF rotation cell A5 (EM-233).

Reads the committed declaration (refusing one that was not committed first), the ETF daily bars the
`fetch-etf-bars` command stored (through the vault reader, Discovery only), the same-universe
benchmark (60/40 NIFTYBEES/GOLDBEES buy-and-hold rebalanced yearly, the same costs and days), and
runs every arm with cash accruing the declared 5.0% a year. The arms go into
`docs/research/profit/screens.jsonl` once. The report adds the sub-period from 2017-11-13 (the
momentum book's start), where the ETF bars are cleanest, for arms and benchmark alike; that
sub-period is reported, not a second look."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import typer
import yaml

from emporos.backtest.vault import VaultedCandleReader
from emporos.cli.cold_storage import DEFAULT_COLD_DIR
from emporos.cli.daily_bars_commands import derived_candle_root
from emporos.cli.etf_bars_commands import DEFAULT_REPORT
from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    DeclarationGate,
    ExperimentDeclarationLoader,
)
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.swing_bars import VaultedDailyBars
from emporos.cli.swing_commands import CAPITAL, NIFTY_50
from emporos.cli.vault_files import VaultFiles
from emporos.core.clock import SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.fees import TradeProduct
from emporos.persistence.candle_cache import CandleCacheFiles, ColdArchiveFiles, FileCandleReader
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.adjustments import AdjustmentLedger
from emporos.research.gap_classes import GapClass
from emporos.research.partition import DISCOVERY
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.cells import ROTATION_CELLS, CellEnvironment
from emporos.research.swing.costs import BENCHMARK, SwingCostModel
from emporos.research.swing.ledger import (
    DEFAULT_PNL_DIR,
    DEFAULT_PROFIT_SCREENS,
    DailyPnlStore,
    JsonlSwingLedger,
)
from emporos.research.swing.loading import SwingDatasetBuilder
from emporos.research.swing.metrics import SwingMetrics, SwingStats
from emporos.research.swing.regime import IndexSeries, YearlyCalendar
from emporos.research.swing.report import format_report
from emporos.research.swing.rules import LossStop
from emporos.research.swing.runner import CellReport, SwingCellRunner
from emporos.research.swing.screen import SwingScreenRun
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


def _sub_period(report: CellReport) -> list[str]:
    lines = [
        "",
        f"sub-period from {SUB_PERIOD_START} (report only, benchmark costs; the arms' own runs,"
        " sliced):",
        f"{'arm':<40} {'CAGR':>7} {'Sharpe':>6} {'m+ all':>7} {'m+ exp':>7} {'worst':>7} "
        f"{'maxDD':>6} {'trips':>5}",
    ]
    for arm in report.arms:
        s = SwingMetrics.of(arm.outcome.arm.slice_from(SUB_PERIOD_START))
        lines.append(_sub_row(arm.label, s))
    return lines


def _sub_row(label: str, s: SwingStats) -> str:
    def pct(v: float | None, d: int = 1) -> str:
        return "n/a" if v is None else f"{v * 100:.{d}f}%"

    sharpe = "n/a" if s.net_sharpe is None else f"{s.net_sharpe:.2f}"
    return (
        f"{label:<40} {pct(s.net_cagr):>7} {sharpe:>6} {pct(s.positive_month_share, 0):>7} "
        f"{pct(s.positive_month_share_exposed, 0):>7} {pct(s.worst_month):>7} "
        f"{pct(s.max_drawdown):>6} {s.round_trips:>5}"
    )


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
        audit = yaml.safe_load(etf_report.read_text(encoding="utf-8"))
        ids = {e["symbol"]: e["instrument_id"] for e in audit["etfs"]}
        first = min(date.fromisoformat(e["first_day"]) for e in audit["etfs"])
        settings = Settings.default()
        cold = VaultedDailyBars(
            VaultedCandleReader(
                FileCandleReader(
                    [ColdArchiveFiles(Path(settings.cold_archive_dir or DEFAULT_COLD_DIR))],
                    memoize=False,
                ),
                VaultFiles().load(),
            )
        )
        nifty = VaultedDailyBars(
            VaultedCandleReader(
                FileCandleReader(
                    [CandleCacheFiles(root or derived_candle_root(settings))], memoize=False
                ),
                VaultFiles().load(),
            )
        )
        index = IndexSeries(nifty.bars(NIFTY_50, DISCOVERY.first, DISCOVERY.last))
        built = SwingDatasetBuilder(cold, AdjustmentLedger(), index).build(
            list(ids.values()), first, DISCOVERY.last
        )
        if built.names_without_bars:
            raise ValueError(f"no bars for {built.names_without_bars}: run fetch-etf-bars first")
        # every arm and the benchmark are judged over the same days: from the first session on which
        # ALL the ETFs have 253 sessions of history (the earlier bars are history a signal reads)
        start_day = max(built.dataset.series(i).days[252] for i in built.dataset.instrument_ids)
        schedule = FeeScheduleLibrary.from_directory(product=TradeProduct.DELIVERY).earliest
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
    for line in _sub_period(report):
        typer.echo(line)
    sub = SwingMetrics.of(benchmark.slice_from(SUB_PERIOD_START))
    typer.echo(_sub_row("BENCHMARK 60/40", sub))
