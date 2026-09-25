"""`emporos research stress-book <variant>`: survivorship stress of the frozen A4 book (EM-235).

The candidate is `docs/research/profit/candidates/a4-60-40.yaml`. The run re-hashes every input
the candidate pinned and refuses to start if any moved. It runs the FROZEN arm only, in a universe
the variant changes (see `research.swing.stress`), on the same Discovery window, and the book and
its benchmark are both rebuilt on that universe. Each run goes into `screens.jsonl` once, marked
`kind: robustness` (it is counted), with the neighbour share the Discovery cell had, because a
universe change is not a parameter change and there are no neighbours to run."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer

from emporos.cli.book_commands import BOOK_BENCHMARK_LABEL, SUB_PERIOD_START
from emporos.cli.corporate_actions_commands import research_symbols
from emporos.cli.etf_bars_commands import DEFAULT_REPORT
from emporos.cli.experiment_declarations import (
    DEFAULT_DECLARATIONS_DIR,
    DeclarationGate,
    ExperimentDeclarationLoader,
)
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.index_change_commands import CURRENT_LISTS, D1_DIR, current_symbols
from emporos.cli.rotation_commands import BENCHMARK_LABEL as ETF_BENCHMARK_LABEL
from emporos.cli.rotation_commands import BENCHMARK_WEIGHTS, CASH_YIELD
from emporos.cli.swing_commands import DEFAULT_TOKENS
from emporos.cli.swing_worlds import CAPITAL, EtfWorldLoader, StockWorldLoader, delivery_schedule
from emporos.core.clock import SystemClock
from emporos.core.errors import EmporosError
from emporos.research.adjustments import DEFAULT_LEDGER
from emporos.research.d1_universe import DEFAULT_MANIFEST
from emporos.research.gap_classes import GapClass
from emporos.research.index_membership import (
    DEFAULT_ALIASES,
    DEFAULT_CHANGES,
    AsOfMembership,
    MembershipTimeline,
    load_aliases,
    load_changes,
    resolve_symbols,
)
from emporos.research.swing.book_cell import SleeveBookCell, SleeveInputs
from emporos.research.swing.book_report import format_book_report
from emporos.research.swing.book_runner import BookReport, BookRunner
from emporos.research.swing.book_screen import BookScreenRun
from emporos.research.swing.bootstrap import BlockBootstrap
from emporos.research.swing.candidate import FrozenCandidate
from emporos.research.swing.cells import CellEnvironment
from emporos.research.swing.ledger import (
    DEFAULT_PNL_DIR,
    DEFAULT_PROFIT_SCREENS,
    DailyPnlStore,
    JsonlSwingLedger,
)
from emporos.research.swing.rules import LossStop
from emporos.research.swing.stress import (
    AsOf,
    LateJoinersOut,
    LateListedOut,
    StressedUniverse,
    UniverseStress,
    late_joiners,
    late_listed,
)

DEFAULT_CANDIDATE = Path("docs/research/profit/candidates/a4-60-40.yaml")
VARIANTS = ("late-listed-out", "late-joiners-out", "as-of-membership")

_VARIANT = typer.Argument(..., help=f"One of: {', '.join(VARIANTS)}")
_CANDIDATE = typer.Option(DEFAULT_CANDIDATE, help="The frozen candidate file.")
_MANIFEST = typer.Option(DEFAULT_MANIFEST, help="The committed D1 universe manifest.")
_TOKENS = typer.Option(DEFAULT_TOKENS, help="The D1 symbol-to-token table.")
_ADJUSTMENTS = typer.Option(DEFAULT_LEDGER, help="The corporate-action adjustment ledger.")
_ETF_REPORT = typer.Option(DEFAULT_REPORT, help="The fetch-etf-bars audit (the ETFs' ids).")
_CHANGES = typer.Option(DEFAULT_CHANGES, help="The parsed index-change file.")
_ALIASES = typer.Option(DEFAULT_ALIASES, help="Old symbol to current symbol.")
_D1 = typer.Option(D1_DIR, help="The directory of today's constituent lists.")
_LEDGER = typer.Option(DEFAULT_PROFIT_SCREENS, help="The append-only Track A/B screen ledger.")
_DISCOVERY = typer.Option(DEFAULT_PROFIT_SCREENS, help="Where the candidate's Discovery arm is.")
_PNL = typer.Option(DEFAULT_PNL_DIR, help="Where each arm's daily P&L file is written.")
_ROOT = typer.Option(None, help="Derived-candle root (default: beside the candle cache).")
_PATHS = typer.Option(10_000, help="Bootstrap paths: the plan's number; fewer only for tests.")


class MembershipLoader:
    """The as-of membership from today's two lists, the parsed changes and the symbol aliases."""

    def __init__(self, d1: Path, changes: Path, aliases: Path) -> None:
        self._d1, self._changes, self._aliases = d1, changes, aliases

    def load(self, symbols: dict[str, str]) -> tuple[AsOfMembership, list[date], dict[str, str]]:
        changes = load_changes(self._changes)
        timelines = [
            MembershipTimeline(index, current_symbols(self._d1 / name), changes)
            for index, name in CURRENT_LISTS.items()
        ]
        resolved = resolve_symbols(symbols, load_aliases(self._aliases))
        return AsOfMembership(timelines, resolved), [c.effective for c in changes], resolved


def _discovery_neighbour_share(ledger: Path, screen_id: str) -> float | None:
    for line in ledger.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["screen_id"] == screen_id:
            share = row["neighbour_share"]
            return None if share is None else float(share)
    raise ValueError(f"{screen_id} is not in {ledger}: the candidate's Discovery arm is missing")


def _never_members(
    membership: AsOfMembership, ids: tuple[str, ...], first: date, last: date
) -> list[str]:
    """D1 names that were not a member on any day of the window, sampled monthly."""
    seen: set[str] = set()
    day = first
    while day <= last:
        seen |= membership.instruments_on(day)
        day = date(day.year + (day.month // 12), day.month % 12 + 1, 1)
    return sorted(i for i in ids if i not in seen)


def _preface(
    variant: UniverseStress, world: StressedUniverse, names: int, membership: AsOfMembership,
    never: list[str], candidate: FrozenCandidate,
) -> list[str]:  # fmt: skip
    lines = [
        f"STRESS {variant.name} of frozen candidate {candidate.name} "
        f"(screen {candidate.screen_id}, code {candidate.code_commit[:7]}), robustness look, "
        "Discovery window, same book, same costs; the book and its benchmark are both on the "
        "stressed universe.",
        f"equity universe: {names} names before, {len(world.dataset.instrument_ids)} after; "
        f"{len(world.removed)} removed"
        + (", as-of membership applied (a name trades only while a member)"
           if world.membership is not None else ""),
    ]  # fmt: skip
    lines += [f"  out: {i} ({why})" for i, why in sorted(world.removed.items())]
    if world.membership is not None or variant.name == "late-joiners-out":
        cov = membership.coverage_start
        lines.append(
            f"membership coverage: changes on record from {cov}; inconsistencies with today's "
            f"lists left: {len(membership.inconsistencies)} (each is a rename, a later exit no "
            "notice records, or a missing notice; see index-changes-report.yaml)"
        )
    if world.membership is not None:
        lines.append(
            f"D1 names that were a member on no day of the window (never tradable here; a rename "
            f"without an alias also lands here): {len(never)}: {', '.join(never) or 'none'}"
        )
    return lines


def research_stress_book(
    variant: str = _VARIANT,
    candidate_file: Path = _CANDIDATE,
    manifest: Path = _MANIFEST,
    tokens: Path = _TOKENS,
    adjustments: Path = _ADJUSTMENTS,
    etf_report: Path = _ETF_REPORT,
    changes: Path = _CHANGES,
    aliases: Path = _ALIASES,
    d1: Path = _D1,
    ledger: Path = _LEDGER,
    discovery_ledger: Path = _DISCOVERY,
    pnl_dir: Path = _PNL,
    root: Path | None = _ROOT,
    bootstrap_paths: int = _PATHS,
) -> None:
    """Run the frozen A4 candidate on a survivorship-stressed equity universe."""
    try:
        if variant not in VARIANTS:
            raise ValueError(f"no stress {variant!r} (known: {', '.join(VARIANTS)})")
        candidate = FrozenCandidate.load(candidate_file)
        candidate.verify()
        cell = SleeveBookCell()
        if candidate.cell != cell.slug:
            raise ValueError(f"{candidate.cell} is not the book cell {cell.slug}")
        DeclarationGate(ExperimentDeclarationLoader(), GitRepository()).load(
            DEFAULT_DECLARATIONS_DIR / f"{cell.slug}.yaml"
        )
        stock = StockWorldLoader(manifest, adjustments, root).load()
        etf = EtfWorldLoader(etf_report, root).load()
        symbols = research_symbols(manifest, tokens)
        membership, change_days, resolved = MembershipLoader(d1, changes, aliases).load(symbols)
        start = date.fromisoformat("2017-11-13")
        last = stock.built.dataset.calendar[-1]
        full = stock.built.dataset
        stress: UniverseStress
        if variant == "late-listed-out":
            stress = LateListedOut(start)
        elif variant == "late-joiners-out":
            joiners = late_joiners(membership, resolved, start, last, change_days)
            stress = LateJoinersOut(start, joiners)
        else:
            stress = AsOf(membership)
        world = stress.apply(full)
        stock_env = CellEnvironment(world.dataset, stock.regime, LossStop(), None, stock.index)
        etf_env = CellEnvironment(etf.built.dataset, None, LossStop(), None, etf.index)
        names = {instrument: symbol for symbol, instrument in etf.ids.items()}
        inputs = SleeveInputs(
            {etf.ids[s]: w for s, w in BENCHMARK_WEIGHTS.items()},
            CASH_YIELD, CAPITAL, start, frozenset(names), world.membership,
        )  # fmt: skip
        sleeves = cell.sleeves(stock_env, etf_env, inputs)
        schedule = delivery_schedule()
        screen = BookScreenRun(
            sleeves, schedule, CAPITAL, start, BlockBootstrap(paths=bootstrap_paths)
        )
        runner = BookRunner(
            screen, cell, stock_env, CAPITAL, f"{stock.label}+etf3+{stress.name}",
            JsonlSwingLedger(ledger), DailyPnlStore(pnl_dir), SystemClock().now(),
            stock.built.of_class(GapClass.REAL),
            f"{BOOK_BENCHMARK_LABEL} (ETF leg: {ETF_BENCHMARK_LABEL}), on the stressed universe",
            sum(s.max_positions for s in sleeves), "robustness",
            _discovery_neighbour_share(discovery_ledger, candidate.screen_id),
        )  # fmt: skip
        grid = {"split": [candidate.arm["split"]], "stress": [stress.name]}
        report: BookReport = runner.run(grid)
        never = _never_members(membership, full.instrument_ids, start, last)
        preface = _preface(stress, world, len(full.instrument_ids), membership, never, candidate)
        preface.append(
            f"late-listed in the full universe (first session after {start}): "
            f"{len(late_listed(full, start))} names"
        )
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"stress-book failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in format_book_report(report, names, SUB_PERIOD_START, preface):
        typer.echo(line)
