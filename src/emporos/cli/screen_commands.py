"""`emporos research screen <slug>` — S1 and S2 for one declared search-map cell (EM-191 §7.3).

Reads only the local candle cache, through the vault, over the Discovery split; the declaration must
be committed first. Every arm is a counted look: it is appended to
`docs/research/edge-search/screens.jsonl` (one line per arm, once), which program-wide N counts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import typer

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.backtest.vault import VaultedCandleReader
from emporos.cli.backtest_runtime import candle_cache_root
from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    DeclarationGate,
    ExperimentDeclarationLoader,
)
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.vault_files import VaultFiles
from emporos.core.clock import IST, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.research_experiments import ExperimentDeclaration
from emporos.domain.sizing import SizeResolver
from emporos.persistence.candle_cache import CandleCacheFiles, FileCandleReader
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.cell_run import ArmResult, BarSource, CellScreenRun, ScanFactory
from emporos.research.history_audit import HistoryAudit
from emporos.research.partition import DISCOVERY, DataSplit
from emporos.research.scans.base import ScanExecution, SignalScan
from emporos.research.scans.orb_rvol import OrbRvolParameters, orb_rvol_scan
from emporos.research.scans.orb_rvol import declared_arms as orb_rvol_arms
from emporos.research.scans.range_compression import CompressionParameters, range_compression_scan
from emporos.research.scans.range_compression import declared_arms as compression_arms
from emporos.research.scans.raw_gap import RawGapParameters, declared_arms, raw_gap_scan
from emporos.research.scans.regime_gate import TrailingPercentileDays, session_openings
from emporos.research.scans.shock_reversal import ShockReversalParameters, shock_reversal_scan
from emporos.research.scans.shock_reversal import declared_arms as shock_reversal_arms
from emporos.research.scans.vix_shock_reversal import VixReversalParameters, vix_shock_reversal_scan
from emporos.research.scans.vix_shock_reversal import declared_arms as vix_reversal_arms
from emporos.research.screen_costs import ScreenCostModel, ScreenCostScenario
from emporos.research.screen_evaluator import ScreenEvaluator
from emporos.research.screen_ledger import JsonlScreenLedger
from emporos.risk.config import RiskLimitsLoader

DEFAULT_SCREENS_FILE = Path("docs/research/edge-search/screens.jsonl")
_SLUG = typer.Argument(..., help="A declared cell: config/experiments/<slug>.yaml")
_LEDGER = typer.Option(DEFAULT_SCREENS_FILE, help="The append-only screen ledger.")


class LocalBars:
    """5m bars from the local candle cache, through the vault, for one split at a time."""

    def __init__(self, reader: VaultedCandleReader) -> None:
        self._reader = reader

    def bars(self, instrument_id: str, split: DataSplit) -> list[Candle]:
        start = datetime.combine(split.first, time(0, 0), tzinfo=IST)
        end = datetime.combine(split.last + timedelta(days=1), time(0, 0), tzinfo=IST)
        return asyncio.run(self._reader.get_range(instrument_id, Timeframe.M5, start, end))


class ScreenCell(Protocol):
    """One declared cell: its arms, how to scan an arm, and how to name one in the table."""

    slug: str

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]: ...

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> SignalScan: ...

    def label(self, point: Mapping[str, str]) -> str: ...

    def prepare(self, bars: BarSource) -> None:
        """Load whatever the cell needs besides the instruments' own bars (a regime series)."""
        ...


class _NoPreparation:
    def prepare(self, bars: BarSource) -> None:
        return None


class RawGapCell(_NoPreparation):
    """The recipe for cell L3-raw-gap-hold-to-close."""

    slug = "l3-raw-gap-hold-to-close"

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]:
        return [p.as_point() for p in declared_arms(declaration.parameter_grid)]

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> SignalScan:
        return raw_gap_scan(RawGapParameters.from_point(point), execution)

    def label(self, point: Mapping[str, str]) -> str:
        return f"{point['gap_threshold_pct']}% {point['direction']} bar{point['entry_bar']}"


class OrbRvolCell(_NoPreparation):
    """The recipe for cell L3-orb-high-rvol-wide-range."""

    slug = "l3-orb-high-rvol-wide-range"

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]:
        return [p.as_point() for p in orb_rvol_arms(declaration.parameter_grid)]

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> SignalScan:
        return orb_rvol_scan(OrbRvolParameters.from_point(point), execution)

    def label(self, point: Mapping[str, str]) -> str:
        return (
            f"rvol>={point['rvol_min']} width>={point['or_width_min_bps']}bps exit {point['exit']}"
        )


class ShockReversalCell(_NoPreparation):
    """The recipe for the first-hour gap-reversal cells: one scan, one declaration per cell."""

    def __init__(self, slug: str) -> None:
        self.slug = slug

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]:
        return [p.as_point() for p in shock_reversal_arms(declaration.parameter_grid)]

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> SignalScan:
        return shock_reversal_scan(ShockReversalParameters.from_point(point), execution)

    def label(self, point: Mapping[str, str]) -> str:
        return f"gap>={point['gap_threshold_pct']}% retrace>={point['retrace_min']}"


class CompressionCell(_NoPreparation):
    """The recipe for cell L4-nr7-inside-day-breakout."""

    slug = "l4-nr7-inside-day-breakout"

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]:
        return [p.as_point() for p in compression_arms(declaration.parameter_grid)]

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> SignalScan:
        return range_compression_scan(CompressionParameters.from_point(point), execution)

    def label(self, point: Mapping[str, str]) -> str:
        return f"{point['condition']} break buffer {point['buffer_bps']}bps"


class VixRegimeCell:
    """The recipe for cell L4-india-vix-regime: the INDIA VIX's opens gate each arm's days."""

    slug = "l4-india-vix-regime"
    VIX_SERIES_ID = "NSE:99926017"  # config/reference_series.yaml, token of India VIX

    def __init__(self) -> None:
        self._openings: dict[date, Decimal] | None = None

    def prepare(self, bars: BarSource) -> None:
        self._openings = session_openings(bars.bars(self.VIX_SERIES_ID, DISCOVERY))
        if not self._openings:
            raise ValueError("no INDIA VIX bars in the cache: run `history fetch-reference` first")

    def arms(self, declaration: ExperimentDeclaration) -> list[dict[str, str]]:
        return [p.as_point() for p in vix_reversal_arms(declaration.parameter_grid)]

    def scan(self, point: Mapping[str, str], execution: ScanExecution) -> SignalScan:
        if self._openings is None:
            raise ValueError("the VIX series was not loaded before scanning")
        parameters = VixReversalParameters.from_point(point)
        gate = TrailingPercentileDays.from_openings(self._openings, parameters.vix_min_percentile)
        return vix_shock_reversal_scan(parameters, execution, gate)

    def label(self, point: Mapping[str, str]) -> str:
        return (
            f"gap>={point['gap_threshold_pct']}% retrace>={point['retrace_min']} "
            f"vix>=p{point['vix_min_percentile']}"
        )


CELLS: Mapping[str, ScreenCell] = {
    c.slug: c
    for c in (
        RawGapCell(),
        OrbRvolCell(),
        ShockReversalCell("l3-first-hour-shock-reversal"),
        ShockReversalCell("l3-first-hour-reversal-large-gap"),
        CompressionCell(),
        VixRegimeCell(),
    )
}


def _table(recipe: ScreenCell, results: list[ArmResult]) -> list[str]:
    lines = [
        f"{'arm':<40} {'verdict':<14} {'trades':>6} {'gross%':>8} {'net%':>8} {'t':>7} "
        f"{'med|mv|%':>9}"
    ]
    for r in results:
        x = r.result
        name = recipe.label(r.parameters)

        def pct(v: object) -> str:
            return "n/a" if v is None else f"{float(v) * 100:.3f}"  # type: ignore[arg-type]

        lines.append(
            f"{name:<40} {x.verdict.value:<14} {x.trades:>6} {pct(x.gross_mean):>8} "
            f"{pct(x.net_mean):>8} {'n/a' if x.net_t is None else f'{float(x.net_t):.2f}':>7} "
            f"{pct(x.median_abs_move):>9}"
        )
    return lines


def research_screen(slug: str = _SLUG, ledger: Path = _LEDGER) -> None:
    """Run S1 and S2 for every arm of a declared cell over the Discovery split."""
    try:
        recipe = CELLS.get(slug)
        if recipe is None:
            raise ValueError(f"no cell recipe for {slug!r} (known: {', '.join(sorted(CELLS))})")
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
        reader = VaultedCandleReader(
            FileCandleReader([CandleCacheFiles(candle_cache_root(Settings.default()))]),
            VaultFiles().load(),
        )
        factory: ScanFactory = recipe.scan
        run = CellScreenRun(
            factory, evaluator, JsonlScreenLedger(ledger), SystemClock(), HistoryAudit.load()
        )
        typer.echo(
            f"{slug}: {DISCOVERY.first}..{DISCOVERY.last}, "
            f"{size.position_value} rupees per position"
        )
        local_bars = LocalBars(reader)
        recipe.prepare(local_bars)
        results = run.run(slug, recipe.arms(declaration), local_bars, size)
    except (EmporosError, ValueError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"screen failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in _table(recipe, results):
        typer.echo(line)
    if any(r.result.advisory for r in results):
        typer.echo("advisory: this scan is not parity-proven against an engine strategy (F3b)")
