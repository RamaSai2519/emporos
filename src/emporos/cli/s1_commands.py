"""`emporos research s1-entry-counts` and `run-s1-cash`: the operator's method on stock crossings
(EM-219, declaration `s1-size-target-trail`, stage 1 cash book).

Offline research: the crossing ledger (Track R), the vault-guarded 5-minute archive, no broker, no
model. `s1-entry-counts` prints counts only (signals and entries taken per month); `run-s1-cash`
runs the 36 arms with their 200-run random-entry controls and writes the report and the ledger."""

from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from pathlib import Path

import typer

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.cli.intraday_bars import VaultedIntradayBars
from emporos.core.clock import SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.core.paths import research_dir
from emporos.eventtrader.replay.program_costs import IntradayCosts
from emporos.eventtrader.replay.vaulted_market import AdjustedBarLoader
from emporos.eventtrader.stages.models import Side
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.adjustments import AdjustmentLedger, PriceAdjuster
from emporos.research.s1.arms import cash_arms
from emporos.research.s1.bars import CandleBarStore
from emporos.research.s1.control import RandomEntries
from emporos.research.s1.costs import CostCurve
from emporos.research.s1.engine import BookEngine, BookLimits
from emporos.research.s1.metrics import Bars
from emporos.research.s1.report import S1Ledger, arm_table
from emporos.research.s1.runner import ArmRunner, month_table, run_all
from emporos.research.s1.signals import Signal, load_stock_signals, signals_by_day
from emporos.research.screen_costs import ScreenCostModel

NIFTY_ID = "NSE:99926000"
DEV_FIRST, DEV_LAST = date(2024, 1, 1), date(2024, 12, 31)
WARM_FROM = date(2023, 12, 1)  # the ATR of the first entries needs the session before
DEFAULT_REPORTS = Path("docs/research/profit/reports/s1")
DEFAULT_CROSSINGS = research_dir() / "atlas" / "crossing-ledger.parquet"
DEFAULT_BARS = research_dir() / "s1-bars"

_FIRST = typer.Option(datetime(2024, 1, 1), "--first", formats=["%Y-%m-%d"], help="First day.")
_LAST = typer.Option(datetime(2024, 12, 31), "--last", formats=["%Y-%m-%d"], help="Last day.")
_CROSSINGS = typer.Option(DEFAULT_CROSSINGS, help="The crossing ledger (Track R).")
_BARS = typer.Option(DEFAULT_BARS, help="Per-name 5-minute OHLCV cache.")
_REPORTS = typer.Option(DEFAULT_REPORTS, help="Where the report goes.")
_WORKERS = typer.Option(6, min=1, help="Worker processes.")
_RUNS = typer.Option(200, min=1, help="Random-entry control runs per arm.")
_LEDGER = typer.Option(Path("docs/research/profit/screens.jsonl"), help="Counted looks.")


def _loader() -> AdjustedBarLoader:
    return AdjustedBarLoader(
        VaultedIntradayBars.from_settings(Settings.default()),
        PriceAdjuster(AdjustmentLedger.load()),
    )


def _store(last: date, cache: Path) -> CandleBarStore:
    tag = f"{WARM_FROM.isoformat()}-{last.isoformat()}"
    return CandleBarStore(_loader(), WARM_FROM, last, cache, tag)


def _warm_one(job: tuple[str, date, Path]) -> str:
    instrument_id, last, cache = job
    _store(last, cache).warm(instrument_id)
    return instrument_id


def _curves() -> dict[int, CostCurve]:
    exact = IntradayCosts(
        ScreenCostModel(EarliestBeforeFirst(FeeScheduleLibrary.from_directory())),
        BenchmarkLoader().load(),
    )
    return {1: CostCurve(exact, Side.LONG), -1: CostCurve(exact, Side.SHORT)}


def _prepare(
    first: date,
    last: date,
    crossings: Path,
    cache: Path,
    workers: int,
    limits: BookLimits | None = None,
) -> tuple[BookEngine, list[date], dict[date, list[Signal]]]:
    if last > DEV_LAST:
        raise ValueError("Dev is 2024: later days belong to the back-test years and Test")
    signals = load_stock_signals(crossings, first, last)
    ids = sorted({s.instrument_id for s in signals} | {NIFTY_ID})
    typer.echo(f"{len(signals):,} stock crossings, {len(ids) - 1} names; reading bars")
    with ProcessPoolExecutor(workers) as pool:
        list(pool.map(_warm_one, [(i, last, cache) for i in ids]))
    store = _store(last, cache)
    sessions = [d for d in store.sessions(NIFTY_ID) if first <= d <= last]
    return BookEngine(store, _curves(), limits), sessions, signals_by_day(signals)


def research_s1_entry_counts(
    first: datetime = _FIRST,
    last: datetime = _LAST,
    crossings: Path = _CROSSINGS,
    cache: Path = _BARS,
    reports: Path = _REPORTS,
    workers: int = _WORKERS,
) -> None:
    """Signals and entries taken per month for the cash book. COUNTS ONLY: nothing about P&L."""
    try:
        # No loss limits here: a kill would stop entries and so leak the P&L into a count.
        limits = BookLimits(daily_loss=math.inf, total_loss=math.inf)
        engine, sessions, signals = _prepare(
            first.date(), last.date(), crossings, cache, workers, limits
        )
        outcomes = run_all(ArmRunner(engine, sessions, signals), cash_arms(), workers)
    except (EmporosError, ValueError, OSError, KeyError) as error:
        typer.secho(f"s1-entry-counts failed: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    lines = [
        f"S1 cash book, stock crossings k=1.5, {first.date()}..{last.date()}: counts only",
        f"{sum(len(v) for v in signals.values()):,} signals over {len(sessions)} sessions",
        "(loss limits off for counting, so the counts do not depend on any P&L)",
        "", *month_table(outcomes, signals),
    ]  # fmt: skip
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "cash-entry-counts.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    typer.echo("\n".join(lines))


def research_run_s1_cash(
    first: datetime = _FIRST,
    last: datetime = _LAST,
    crossings: Path = _CROSSINGS,
    cache: Path = _BARS,
    reports: Path = _REPORTS,
    ledger: Path = _LEDGER,
    workers: int = _WORKERS,
    runs: int = _RUNS,
) -> None:
    """The 36 cash arms on Dev, each against its random-entry control; one counted look each."""
    try:
        engine, sessions, signals = _prepare(first.date(), last.date(), crossings, cache, workers)
        runner = ArmRunner(engine, sessions, signals, RandomEntries(), runs)
        outcomes = run_all(runner, cash_arms(), workers)
    except (EmporosError, ValueError, OSError, KeyError) as error:
        typer.secho(f"run-s1-cash failed: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    bars = Bars()
    window = f"dev-{first.date()}..{last.date()}"
    added = sum(S1Ledger(ledger).append(o, window, bars, SystemClock().now()) for o in outcomes)
    lines = [f"S1 cash book on {window} (Dev, descriptive), {runs} control runs per arm", ""]
    lines += arm_table(outcomes, bars)
    lines += ["", f"{sum(1 for o in outcomes if not o.metrics.failures(bars, o.control_p))} of "
              f"{len(outcomes)} arms pass every bar; {added} looks added to {ledger}"]  # fmt: skip
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "cash-dev-2024.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    typer.echo("\n".join(lines))
