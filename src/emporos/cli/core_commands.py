"""`emporos research screen-core <slug>` — run the ETF core cell C1 (EM-237, PROFIT_PLAN §10).

Reads the committed declaration (refusing one that was not committed first) and the ETF daily bars
the `fetch-etf-bars` command stored (through the vault reader, Discovery only). Each arm is a
monthly target-weight allocation filled at the next session's CLOSE, cash accruing the declared 5.0%
a year, judged on the §10 CORE bar against the 60/40 NIFTYBEES/GOLDBEES buy-and-hold (yearly
rebalance, same costs, same fill rule, same days). Every arm goes into the profit screen ledger
once. The report adds both halves and the slice from 2017-11-13 (the momentum book's window)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from emporos.cli.swing_worlds import CAPITAL, EtfWorld, EtfWorldLoader, delivery_schedule
from emporos.core.clock import SystemClock
from emporos.core.errors import EmporosError
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.cells import arm_label, arm_points
from emporos.research.swing.core_report import CoreRow, core_lines
from emporos.research.swing.core_screen import CoreMeasures, CoreScreenRun, judge_core
from emporos.research.swing.costs import BENCHMARK, SwingCostModel
from emporos.research.swing.ledger import (
    DEFAULT_PNL_DIR,
    DEFAULT_PROFIT_SCREENS,
    DailyPnlStore,
    JsonlSwingLedger,
    SwingIdentity,
    SwingRecord,
)
from emporos.research.swing.regime import YearlyCalendar
from emporos.research.swing.report import format_report, sub_period_lines
from emporos.research.swing.runner import ArmReport, CellReport
from emporos.research.swing.simulator import FillPrice, SwingRun
from emporos.research.swing.trend_core import TrendCorePolicy, TrendCoreRecord
from emporos.research.swing.weighted import WeightedBuyHold

SLUG = "c1-etf-trend-core"
CASH_YIELD = Decimal("0.05")  # declared in c1-etf-trend-core.yaml
PRIOR_SESSIONS = 273  # declared: the first session with 273 prior sessions for all three ETFs
BENCHMARK_WEIGHTS = {"NIFTYBEES-EQ": Decimal("0.6"), "GOLDBEES-EQ": Decimal("0.4")}
FIXED_WEIGHTS = {
    "NIFTYBEES-EQ": Decimal("0.4"), "JUNIORBEES-EQ": Decimal("0.3"), "GOLDBEES-EQ": Decimal("0.3")
}  # fmt: skip
BENCHMARK_LABEL = "60/40 NIFTYBEES/GOLDBEES buy-and-hold rebalanced yearly, next close, same costs"
SUB_PERIOD_START = date(2017, 11, 13)

_SLUG = typer.Argument(SLUG, help="The declared core cell: c1-etf-trend-core")
_ETF_REPORT = typer.Option(DEFAULT_REPORT, help="The fetch-etf-bars audit (the ETFs' ids).")
_LEDGER = typer.Option(DEFAULT_PROFIT_SCREENS, help="The append-only Track A/B screen ledger.")
_PNL = typer.Option(DEFAULT_PNL_DIR, help="Where each arm's daily P&L file is written.")
_ROOT = typer.Option(None, help="Derived-candle root (the NIFTY 50 series, for the gap rule).")
_PATHS = typer.Option(10_000, help="Bootstrap paths: the plan's number; fewer only for tests.")


def _note(record: TrendCoreRecord) -> str:
    return (
        f"{record.decisions} months decided, {record.months_all_cash} all-cash, "
        f"months held per asset {dict(sorted(record.months_held.items()))}"
    )


def research_screen_core(
    slug: str = _SLUG,
    etf_report: Path = _ETF_REPORT,
    ledger: Path = _LEDGER,
    pnl_dir: Path = _PNL,
    root: Path | None = _ROOT,
    bootstrap_paths: int = _PATHS,
) -> None:
    """Screen every arm of the declared ETF core cell over Discovery."""
    try:
        if slug != SLUG:
            raise ValueError(f"no core cell for {slug!r} (known: {SLUG})")
        declaration = DeclarationGate(ExperimentDeclarationLoader(), GitRepository()).load(
            DEFAULT_DECLARATIONS_DIR / f"{slug}.yaml"
        )
        world = EtfWorldLoader(etf_report, root, PRIOR_SESSIONS).load()
        schedule = delivery_schedule()
        benchmark = WeightedBuyHold(
            world.built.dataset,
            {world.ids[s]: w for s, w in BENCHMARK_WEIGHTS.items()},
            SwingCostModel(schedule, BENCHMARK),
            YearlyCalendar(),
            CAPITAL,
            CASH_YIELD,
            world.start_day,
            FillPrice.CLOSE,
        ).run()
        assets = sorted(world.built.dataset.instrument_ids)
        fixed = {world.ids[s]: w for s, w in FIXED_WEIGHTS.items()}
        screen = CoreScreenRun(
            world.built.dataset, schedule, assets, benchmark, CAPITAL, CASH_YIELD, world.start_day,
            BlockBootstrap(paths=bootstrap_paths),
        )  # fmt: skip
        typer.echo(
            f"{slug}: {len(assets)} ETFs, judged from {world.start_day} (273 prior sessions each), "
            f"capital {CAPITAL}, cash accrues {CASH_YIELD:.1%} a year, fills at the next close, "
            f"delivery schedule {schedule.name} (verified: {schedule.verified})"
        )
        reports, rows = _run_arms(
            slug, declaration.parameter_grid, screen, assets, fixed, benchmark, world,
            JsonlSwingLedger(ledger), DailyPnlStore(pnl_dir),
        )  # fmt: skip
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"screen-core failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    report = CellReport(
        slug, reports[0].outcome.arm.days[0], reports[0].outcome.arm.days[-1], tuple(reports),
        (f"assets: {', '.join(assets)}; data {world.built.dataset.calendar[0]}.."
         f"{world.built.dataset.calendar[-1]}",),
        BENCHMARK_LABEL,
    )  # fmt: skip
    for line in format_report(report):
        typer.echo(line)
    typer.echo("")
    for line in core_lines(rows, BENCHMARK_LABEL):
        typer.echo(line)
    runs = [(r.label, r.outcome.arm) for r in reports]
    for line in sub_period_lines([*runs, ("BENCHMARK 60/40", benchmark)], SUB_PERIOD_START):
        typer.echo(line)


def _run_arms(
    slug: str,
    grid: Mapping[str, Sequence[str]],
    screen: CoreScreenRun,
    assets: Sequence[str],
    fixed: Mapping[str, Decimal],
    benchmark: SwingRun,
    world: EtfWorld,
    ledger: JsonlSwingLedger,
    pnl: DailyPnlStore,
) -> tuple[list[ArmReport], list[CoreRow]]:
    reports: list[ArmReport] = []
    rows: list[CoreRow] = []
    now = SystemClock().now()
    for point in arm_points(grid):
        rule = point["rule"]
        outcome = screen.run(lambda rule=rule: TrendCorePolicy(rule, assets, fixed))  # type: ignore[misc]
        measures = CoreMeasures.of(outcome, benchmark)
        verdict = judge_core(measures)
        first_day, last_day = outcome.arm.days[0], outcome.arm.days[-1]
        identity = SwingIdentity(
            slug, point, f"etf3-{len(world.built.audit.findings)}gaps", first_day, last_day,
            CAPITAL, len(assets),
        )  # fmt: skip
        path = pnl.write(identity.screen_id, outcome)
        counted = ledger.record(SwingRecord(identity, outcome, verdict, None, now, path))
        policy = outcome.strategy
        assert isinstance(policy, TrendCorePolicy)
        record = policy.record
        label = arm_label(point)
        reports.append(
            ArmReport(
                point,
                label,
                outcome,
                verdict,
                None,
                False,
                record.first_ranked_day,
                record.first_ranked_day,
                _note(record),
                (),
                counted,
            )  # fmt: skip
        )
        rows.append(CoreRow(label, measures, verdict))
    return reports, rows
