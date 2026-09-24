"""`emporos research screen-ranked <slug>` — S1 and S2 for a cell that ranks across the universe
(EM-216, lane L17; docs/research/edge-search/review-2.md §3).

The same guarantees as `research screen`: Discovery only, through the vault, the declaration must
be committed, and every arm is appended once to the screen ledger that program-wide N counts. The
difference is the run: each session only the day's top-ranked signals across the universe are kept
(`RankedCellScreenRun`), so a ranked cell cannot go through `CellScreenRun`, and it always screens
the committed D1 universe (a rank over 29 names is not the declared cell)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol

import typer

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    DeclarationGate,
    ExperimentDeclarationLoader,
)
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.screen_commands import (
    D1_UNIVERSE,
    DEFAULT_EVENTS_DIR,
    DEFAULT_SCREENS_FILE,
    DEFAULT_TOKEN_TABLE,
    screen_universe,
    vaulted_bars,
)
from emporos.core.clock import SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.research_experiments import ExperimentDeclaration
from emporos.domain.sizing import SizeResolver
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.cell_run import BarSource
from emporos.research.daily_selection import DailyTopKSelection
from emporos.research.partition import DISCOVERY
from emporos.research.ranked_cell_run import RankedArmResult, RankedCellScreenRun, RankedScan
from emporos.research.results_filings import FilingLedger
from emporos.research.scans.base import ScanExecution
from emporos.research.scans.event_days import InstrumentSymbols, results_by_symbol
from emporos.research.scans.index_trend_leader import (
    IndexFirstHour,
    IndexTrendLeaderScan,
    LeaderParameters,
)
from emporos.research.scans.index_trend_leader import declared_arms as leader_arms
from emporos.research.scans.top_shock import (
    EverySession,
    NewsFilter,
    SessionFilter,
    TopShockParameters,
    TopShockScan,
    WithoutResultsReaction,
    declared_arms,
)
from emporos.research.screen_costs import ScreenCostModel, ScreenCostScenario
from emporos.research.screen_evaluator import ScreenEvaluator
from emporos.research.screen_ledger import JsonlScreenLedger
from emporos.risk.config import RiskLimitsLoader

__all__ = [
    "RANKED_CELLS",
    "IndexTrendLeaderCell",
    "RankedCell",
    "TopShockCell",
    "research_screen_ranked",
]

_SLUG = typer.Argument(..., help="A declared ranked cell: config/experiments/<slug>.yaml")
_LEDGER = typer.Option(DEFAULT_SCREENS_FILE, help="The append-only screen ledger.")


class RankedCell(Protocol):
    slug: str
    selection: DailyTopKSelection

    def prepare(self, bars: BarSource) -> None:
        """Load whatever the cell needs besides the instruments' own bars (events, an index)."""
        ...

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]: ...

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> RankedScan: ...

    def label(self, point: Mapping[str, str]) -> str: ...


class TopShockCell:
    """The recipe for L17-daily-top-shock-fade: the top ONE confirmed shock per session."""

    slug = "l17-daily-top-shock-fade"
    selection = DailyTopKSelection(1)

    def __init__(self) -> None:
        self._filters: dict[NewsFilter, SessionFilter] | None = None

    def prepare(self, bars: BarSource) -> None:
        ledger = FilingLedger(
            DEFAULT_EVENTS_DIR / "results-filings.jsonl",
            DEFAULT_EVENTS_DIR / "results-collected.jsonl",
        )
        events: dict[str, list[datetime]] = results_by_symbol(ledger.load())
        if not events:
            raise ValueError("no results events: run `emporos research collect-results` first")
        symbols = InstrumentSymbols.load(DEFAULT_TOKEN_TABLE)
        self._filters = {
            NewsFilter.ALL: EverySession(),
            NewsFilter.NO_RESULTS: WithoutResultsReaction(events, symbols),
        }

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]:
        return [p.as_point() for p in declared_arms(declaration.parameter_grid)]

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> RankedScan:
        if self._filters is None:
            raise ValueError("the results events were not loaded before scanning")
        parameters = TopShockParameters.from_point(point)
        return TopShockScan(parameters, execution, self._filters[parameters.news])

    def label(self, point: Mapping[str, str]) -> str:
        return f"top1 gap>={point['gap_floor_pct']}% news={point['news']}"


class IndexTrendLeaderCell:
    """The recipe for L5-index-trend-day-leader: the top ONE candidate leading the index on a
    strongly trending first hour, ranked by the arm's key."""

    slug = "l5-index-trend-day-leader"
    selection = DailyTopKSelection(1)
    NIFTY_SERIES_ID = "NSE:99926000"  # config/reference_series.yaml, token of Nifty 50

    def __init__(self) -> None:
        self._index: IndexFirstHour | None = None

    def prepare(self, bars: BarSource) -> None:
        self._index = IndexFirstHour.from_bars(bars.bars(self.NIFTY_SERIES_ID, DISCOVERY))
        if not self._index.moves:
            raise ValueError("no NIFTY 50 bars in the cache: run `history fetch-reference` first")

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]:
        return [p.as_point() for p in leader_arms(declaration.parameter_grid)]

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> RankedScan:
        if self._index is None:
            raise ValueError("the NIFTY 50 series was not loaded before scanning")
        return IndexTrendLeaderScan(LeaderParameters.from_point(point), self._index, execution)

    def label(self, point: Mapping[str, str]) -> str:
        return f"top1 index>={point['index_move_min_pct']}% rank={point['rank_key']}"


RANKED_CELLS: Mapping[str, RankedCell] = {
    c.slug: c for c in (TopShockCell(), IndexTrendLeaderCell())
}


def _pct(value: object) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.3f}"  # type: ignore[arg-type]


def _table(recipe: RankedCell, results: list[RankedArmResult]) -> list[str]:
    lines = [
        f"{'arm':<32} {'verdict':<14} {'trades':>6} {'long':>5} {'short':>5} {'days':>5} "
        f"{'gross%':>8} {'net%':>8} {'t':>7} {'med|mv|%':>9}"
    ]
    for r in results:
        x = r.arm.result
        t = "n/a" if x.net_t is None else f"{float(x.net_t):.2f}"
        lines.append(
            f"{recipe.label(r.arm.parameters):<32} {x.verdict.value:<14} {x.trades:>6} "
            f"{r.long_trades:>5} {r.short_trades:>5} {r.candidate_days:>5} "
            f"{_pct(x.gross_mean):>8} {_pct(x.net_mean):>8} {t:>7} {_pct(x.median_abs_move):>9}"
        )
    return lines


def research_screen_ranked(slug: str = _SLUG, ledger: Path = _LEDGER) -> None:
    """Run S1 and S2 for every arm of a declared ranked cell over the Discovery split, on D1."""
    try:
        recipe = RANKED_CELLS.get(slug)
        if recipe is None:
            known = ", ".join(sorted(RANKED_CELLS))
            raise ValueError(f"no ranked cell recipe for {slug!r} (known: {known})")
        declaration = DeclarationGate(ExperimentDeclarationLoader(), GitRepository()).load(
            DEFAULT_DECLARATIONS_DIR / f"{slug}.yaml"
        )
        size = SizeResolver(RiskLimitsLoader().load().max_position_value).resolve(
            declared=declaration.position_value
        )
        config = BenchmarkLoader().load()
        evaluator = ScreenEvaluator(
            ScreenCostModel(EarliestBeforeFirst(FeeScheduleLibrary.from_directory())),
            ScreenCostScenario.from_benchmark(config, "benchmark"),
            ScreenCostScenario.from_benchmark(config, config.adverse_scenario),
        )
        run = RankedCellScreenRun(
            recipe.scan, recipe.selection, evaluator, JsonlScreenLedger(ledger), SystemClock(),
            screen_universe(D1_UNIVERSE),
        )  # fmt: skip
        typer.echo(
            f"{slug}: {DISCOVERY.first}..{DISCOVERY.last}, {size.position_value} rupees per "
            f"position, universe {D1_UNIVERSE}, top {recipe.selection.k} per session"
        )
        bars = vaulted_bars(Settings.default(), wide=True)
        recipe.prepare(bars)
        results = run.run(slug, recipe.arms(declaration), bars, size)
    except (EmporosError, ValueError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"screen failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in _table(recipe, results):
        typer.echo(line)
    if any(r.arm.result.advisory for r in results):
        typer.echo("advisory: this scan is not parity-proven against an engine strategy (F3b)")
