"""`emporos research screen-options <slug>` — a declared Track B cell over Discovery (EM-230).

The same guarantees as the intraday screens: the declaration must be committed first, only the
Discovery window is read, and every arm is appended once to the profit ledger that program-wide N
counts. Inputs come from local files only: the stored F&O archive (`collect-fo-archive`), its
contract specs (`build-fo-specs`), and the NIFTY 50 and India VIX 5-minute series (D2). Nothing here
touches Atlas or the network, and no order can be placed."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import typer

from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    DeclarationGate,
    ExperimentDeclarationLoader,
)
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.fo_archive_commands import DEFAULT_FO_DIR
from emporos.cli.screen_commands import vaulted_bars
from emporos.core.clock import Clock, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.research_experiments import ExperimentDeclaration
from emporos.options.fo_costs import FoFeeScheduleLibrary
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.fo_chain_source import BhavcopyChainSource
from emporos.research.fo_contract_specs import read_specs
from emporos.research.index_daily import daily_closes
from emporos.research.option_screen.b1 import DESIGNS, B1Arms, B1Inputs
from emporos.research.option_screen.events import load_blackout_days
from emporos.research.option_screen.ledger import (
    JsonlOptionLedger,
    OptionIdentity,
    OptionRecord,
    write_daily_pnl,
)
from emporos.research.option_screen.report import (
    arm_lines,
    breakdown_lines,
    closure_lines,
    table,
)
from emporos.research.option_screen.run import ArmResult, B1Screen
from emporos.research.partition import DISCOVERY
from emporos.research.swing.ledger import DEFAULT_PNL_DIR, DEFAULT_PROFIT_SCREENS

__all__ = [
    "B1InputLoader",
    "CommittedDeclarations",
    "DeclarationSource",
    "InputLoader",
    "OptionScreenRun",
    "research_screen_options",
]

B1_SLUG = "b1-nifty-put-spread-ladder"
DEFAULT_BLACKOUTS = Path("config/events/b1-blackout-days.yaml")
NIFTY_SERIES_ID, VIX_SERIES_ID = "NSE:99926000", "NSE:99926017"  # config/reference_series.yaml
NIFTY_STRIKE_STEP = Decimal(50)

_SLUG = typer.Argument(..., help="A declared Track B cell: config/experiments/<slug>.yaml")
_FO_DIR = typer.Option(DEFAULT_FO_DIR, help="The stored F&O archive and its contract specs.")
_LEDGER = typer.Option(DEFAULT_PROFIT_SCREENS, help="The append-only profit screen ledger.")
_PNL = typer.Option(DEFAULT_PNL_DIR, help="Where each arm's daily P&L CSV goes.")


class InputLoader(Protocol):
    def load(self, capital: Decimal) -> B1Inputs: ...


class DeclarationSource(Protocol):
    def load(self, slug: str) -> ExperimentDeclaration: ...


class CommittedDeclarations:
    """Declarations from `config/experiments`, refused unless committed first."""

    def __init__(self, directory: Path = DEFAULT_DECLARATIONS_DIR) -> None:
        self._directory = directory
        self._gate = DeclarationGate(ExperimentDeclarationLoader(), GitRepository())

    def load(self, slug: str) -> ExperimentDeclaration:
        return self._gate.load(self._directory / f"{slug}.yaml")


class B1InputLoader:
    """Reads the cell's inputs from the local files, Discovery days only."""

    def __init__(self, fo_dir: Path, blackouts: Path) -> None:
        self._fo_dir = fo_dir
        self._blackouts = blackouts

    def load(self, capital: Decimal) -> B1Inputs:
        specs_file = self._fo_dir / "contract_specs.parquet"
        if not specs_file.exists():
            raise ValueError(f"{specs_file} is missing: run `research build-fo-specs` first")
        specs = [s for s in read_specs(specs_file) if s.symbol == "NIFTY"]
        bars = vaulted_bars(Settings.default(), wide=False)
        nifty = daily_closes(bars.bars(NIFTY_SERIES_ID, DISCOVERY))
        vix = daily_closes(bars.bars(VIX_SERIES_ID, DISCOVERY))
        if not nifty or not vix:
            raise ValueError("no NIFTY 50 or India VIX bars: run `history fetch-reference` first")
        chains = BhavcopyChainSource(
            FoDayStore(self._fo_dir), specs, "NIFTY", NIFTY_STRIKE_STEP, nifty
        )
        # today's schedule applied to the whole history: statutory rates were lower before 2024-10,
        # so the costs are conservative; the report says so
        fees = FoFeeScheduleLibrary.from_directory().earliest
        return B1Inputs(
            chains, nifty, vix, load_blackout_days(self._blackouts),
            {s.day: frozenset(s.future_expiries) for s in specs}, fees, capital,
        )  # fmt: skip


class OptionScreenRun:
    """Loads a declaration, runs its arms and records them; returns the lines to print."""

    def __init__(
        self,
        declarations: DeclarationSource,
        loader: InputLoader,
        ledger: JsonlOptionLedger,
        pnl_dir: Path,
        clock: Clock,
    ) -> None:
        self._declarations = declarations
        self._loader = loader
        self._ledger = ledger
        self._pnl_dir = pnl_dir
        self._clock = clock

    def run(self, slug: str) -> list[str]:
        if slug not in {d.slug for d in DESIGNS}:
            known = ", ".join(d.slug for d in DESIGNS)
            raise ValueError(f"no Track B recipe for {slug!r} (known: {known})")
        declaration = self._declarations.load(slug)
        if declaration.position_value is None:
            raise ValueError("the declaration must state the capital as position_value")
        capital = declaration.position_value
        arms = B1Arms.declared(declaration.parameter_grid)
        screen = B1Screen(self._loader.load(capital), DISCOVERY.last)
        results = screen.run(arms)
        for result in results:
            self._record(slug, result, capital)
        header = [
            f"{slug}: {screen.first_day}..{DISCOVERY.last}, capital Rs {capital:,}, NIFTY monthly "
            f"options, benchmark and adverse costs (today's fee schedule, unverified, applied to "
            f"the whole history)",
        ]
        details = [line for r in results for line in arm_lines(r)]
        costs = [line for r in results for line in breakdown_lines(r)]
        return [*header, *table(results), "", *details, "", *costs, "", *closure_lines(results)]

    def _record(self, slug: str, result: ArmResult, capital: Decimal) -> None:
        identity = OptionIdentity(
            slug, result.arm.as_point(), "NIFTY", result.outcome.first_day, DISCOVERY.last, capital
        )
        path = write_daily_pnl(self._pnl_dir, identity.screen_id, result)
        self._ledger.record(OptionRecord(identity, result, self._clock.now(), path))


def research_screen_options(
    slug: str = _SLUG,
    fo_dir: Path = _FO_DIR,
    ledger: Path = _LEDGER,
    pnl_dir: Path = _PNL,
    blackouts: Path = DEFAULT_BLACKOUTS,
) -> None:
    """Run every arm of a declared Track B cell over the Discovery split."""
    try:
        run = OptionScreenRun(
            CommittedDeclarations(),
            B1InputLoader(fo_dir, blackouts),
            JsonlOptionLedger(ledger),
            pnl_dir,
            SystemClock(),
        )
        lines = run.run(slug)
    except (EmporosError, ValueError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"screen failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in lines:
        typer.echo(line)
    typer.echo(f"finished {datetime.now():%H:%M}")
