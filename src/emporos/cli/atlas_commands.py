"""`emporos research build-move-ledger`: the Move Ledger and the Crossing Ledger (EM-243, Track R).

Reads the vault-guarded 5-minute archive (split-adjusted from the D1 ledger) for the D1 names, NIFTY
and the sector indices, from 2017 to `--last` (never later than 2024-12-31: 2025 and after belong to
Test and the vault), and writes Parquet ledgers and text reports. Offline, no model, no broker."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from pathlib import Path

import typer

from emporos.cli.corporate_actions_commands import DEFAULT_TOKENS, research_symbols
from emporos.cli.intraday_bars import VaultedIntradayBars
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.core.research_dir import research_dir
from emporos.eventtrader.replay.vaulted_market import AdjustedBarLoader
from emporos.research.adjustments import DEFAULT_LEDGER, AdjustmentLedger, PriceAdjuster
from emporos.research.atlas.crossing_ledger import CrossingLedgerBuilder
from emporos.research.atlas.crossings import CrossingBlock, CrossingRules
from emporos.research.atlas.events import MoveEvent
from emporos.research.atlas.files import write_crossings, write_moves
from emporos.research.atlas.ledger import LedgerRules, MoveLedgerBuilder
from emporos.research.atlas.panel import InstrumentPanel, PanelBuilder
from emporos.research.atlas.report import crossing_lines, move_lines, names_csv
from emporos.research.atlas.series import BarSeries, CachedSeriesSource, CandleSeriesSource
from emporos.research.cause_ledger.groups import DEFAULT_GROUPS_FILE, YamlGroupMap
from emporos.research.d1_universe import DEFAULT_MANIFEST
from emporos.research.market_context.sectors import SectorMapLoader

NIFTY_ID = "NSE:99926000"
WARM_UP_FROM = date(2017, 1, 2)  # 120 sessions of betas and 60 of sigma before the first event
SPAN_LAST = date(2024, 12, 31)  # the atlas year is the last day used: 2025 and after are never read
ATLAS_YEAR = date(2024, 1, 1)
DEFAULT_OUT = Path("docs/research/profit/atlas")  # the reports (small, in git)
DEFAULT_LEDGERS = research_dir() / "atlas"  # the Parquet ledgers (large, durable, not in git)
DEFAULT_CACHE = research_dir() / "atlas-series"
CONSTITUENTS = (
    Path("config/universe/d1/nifty100.csv"),
    Path("config/universe/d1/niftymidcap150.csv"),
)

_FIRST = typer.Option(
    datetime(2017, 11, 1), "--first", formats=["%Y-%m-%d"], help="First event day."
)
_LAST = typer.Option(
    datetime(2024, 12, 31), "--last", formats=["%Y-%m-%d"], help="Last day (at most 2024-12-31)."
)
_OUT = typer.Option(DEFAULT_OUT, help="Where the reports go (small, committed).")
_LEDGERS = typer.Option(DEFAULT_LEDGERS, help="Where the Parquet ledgers go (large, not in git).")
_CACHE = typer.Option(DEFAULT_CACHE, help="Per-name bar arrays (local, rebuilt on demand).")
_WORKERS = typer.Option(6, min=1, help="Processes reading the archive.")


def _cache_tag(last: date) -> str:
    ledger = hashlib.sha256(Path(DEFAULT_LEDGER).read_bytes()).hexdigest()[:8]
    return f"{WARM_UP_FROM.isoformat()}-{last.isoformat()}-{ledger}"


def _source(cache: Path, last: date) -> CachedSeriesSource:
    loader = AdjustedBarLoader(
        VaultedIntradayBars.from_settings(Settings.default()),
        PriceAdjuster(AdjustmentLedger.load()),
    )
    return CachedSeriesSource(
        CandleSeriesSource(loader, WARM_UP_FROM, last), cache, _cache_tag(last)
    )


def _load_one(job: tuple[str, Path, date]) -> str:
    instrument_id, cache, last = job
    _source(cache, last).series(instrument_id)
    return instrument_id


def _ids(symbols: dict[str, str], sectors: Iterable[tuple[str, str]]) -> list[str]:
    return sorted({NIFTY_ID, *symbols.values(), *(index for index, _ in sectors)})


def research_build_move_ledger(
    first: datetime = _FIRST,
    last: datetime = _LAST,
    out: Path = _OUT,
    ledgers: Path = _LEDGERS,
    cache: Path = _CACHE,
    workers: int = _WORKERS,
) -> None:
    """Build the ledgers for the D1 names, NIFTY and the sector indices, 2017-11..2024."""
    if last.date() > SPAN_LAST:
        typer.secho("the atlas never reads 2025 or later here", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    try:
        symbols = research_symbols(DEFAULT_MANIFEST, DEFAULT_TOKENS)
        sector_map = SectorMapLoader().load(CONSTITUENTS).by_symbol
        sector_of = {s: v for s, v in sector_map.items() if s in symbols}
        ids = _ids(symbols, sector_of.values())
        typer.echo(f"{len(symbols)} names, {len(set(sector_of.values()))} sector indices, NIFTY")
        with ProcessPoolExecutor(workers) as pool:
            for done, _ in enumerate(
                pool.map(_load_one, [(i, cache, last.date()) for i in ids]), 1
            ):
                if done % 20 == 0:
                    typer.echo(f"  {done}/{len(ids)} series read")
        source = _source(cache, last.date())
        nifty = source.series(NIFTY_ID)
        sessions = _sessions(nifty, WARM_UP_FROM, last.date())
        builder = PanelBuilder(sessions)
        panels: dict[str, InstrumentPanel] = {i: builder.build(source.series(i)) for i in ids}
        groups = YamlGroupMap.load(DEFAULT_GROUPS_FILE)
        rules = LedgerRules(first_event=first.date())
        moves = MoveLedgerBuilder(sessions, groups, rules=rules)
        fits = moves.fit(panels, NIFTY_ID, sector_of, symbols)
        events = moves.build(fits)
        crossings = CrossingLedgerBuilder(sessions, CrossingRules(first_day=first.date())).build(
            fits
        )
        _write(out, ledgers, events, crossings)
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"build-move-ledger failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error


def _sessions(nifty: BarSeries, first: date, last: date) -> tuple[date, ...]:
    from emporos.core.clock import IST

    days = {datetime.fromtimestamp(int(ts), IST).date() for ts in nifty.ts}
    return tuple(sorted(d for d in days if first <= d <= last))


def _write(out: Path, ledgers: Path, events: list[MoveEvent], crossings: CrossingBlock) -> None:
    atlas = [e for e in events if e.day >= ATLAS_YEAR]
    before = [e for e in events if e.day < ATLAS_YEAR]
    write_moves(ledgers / "move-ledger-2024.parquet", atlas)
    write_moves(ledgers / "move-ledger-2017-2023.parquet", before)
    (out / "move-ledger-names.csv").write_text(names_csv(events), encoding="utf-8")
    (out / "move-ledger-report.txt").write_text("\n".join(move_lines(events)) + "\n", "utf-8")
    write_crossings(ledgers / "crossing-ledger.parquet", crossings)
    (out / "crossing-ledger-report.txt").write_text(
        "\n".join(crossing_lines(crossings)) + "\n", "utf-8"
    )
    typer.echo(
        f"{len(events):,} move events, {len(crossings):,} crossings: ledgers in {ledgers}, "
        f"reports in {out}"
    )
