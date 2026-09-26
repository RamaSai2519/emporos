"""The daily posture's inputs: the global-cue collection and a look at what a morning sees
(PROFIT_PLAN §12.3, §8, EM-239). This module is a composition root: it builds the concrete bar
loader, event store and cue files and hands `NumericPostureInputs` its sections."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import httpx
import typer

from emporos.cli.corporate_actions_commands import DEFAULT_TOKENS, research_symbols
from emporos.cli.intraday_bars import VaultedIntradayBars
from emporos.core.clock import IST, AsyncioSleeper, Sleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.core.paths import research_dir
from emporos.eventtrader.events import EventStore
from emporos.research.d1_universe import DEFAULT_MANIFEST
from emporos.research.filings.collector import CollectionHalted
from emporos.research.filings.event_store import DEFAULT_EVENT_DIR, ParquetEventStore
from emporos.research.filings.polite import PoliteGet, SourceRefused
from emporos.research.filings.raw_store import FetchLedger
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.market_context.bars import BarLoader, BarSeriesCache
from emporos.research.market_context.breadth import Breadth, SessionCloseTable
from emporos.research.market_context.builder import AsOfContextBuilder, SectorMap
from emporos.research.market_context.global_cues import (
    CUES,
    DEFAULT_CUES_LEDGER,
    DEFAULT_CUES_RAW_DIR,
    FEEDS,
    CueCollector,
    CueFeed,
    GlobalCues,
)
from emporos.research.market_context.open_interest import FoOpenInterest
from emporos.research.market_context.posture_state import (
    BreadthSection,
    FilingCounts,
    GlobalCuesSection,
    IndexState,
    NumericPostureInputs,
    PostureSection,
)
from emporos.research.market_context.sectors import DEFAULT_SECTOR_TABLE, SectorMapLoader

NIFTY_ID = "NSE:99926000"
VIX_ID = "NSE:99926017"
FIRST_DAY = datetime(2024, 1, 1)
LAST_DAY = datetime(2026, 3, 18)
DEFAULT_FO_STOCK_DIR = research_dir() / "fo_stock_v1"
DEFAULT_CLOSES_FILE = research_dir() / "market-context" / "session-closes.json"

_FROM = typer.Option(FIRST_DAY, formats=["%Y-%m-%d"], help="First day.")
_TO = typer.Option(LAST_DAY, "--to", formats=["%Y-%m-%d"], help="Last day (in).")
_RAW = typer.Option(DEFAULT_CUES_RAW_DIR, help="Where each FRED reply is kept verbatim (local).")
_LEDGER = typer.Option(DEFAULT_CUES_LEDGER, help="The append-only fetch ledger.")
_GAP = typer.Option(3.0, help="Seconds between requests (at least 1).")
_SOURCE = typer.Option("yahoo", help="Cue source: yahoo or fred.")
_EVENTS = typer.Option(DEFAULT_EVENT_DIR, help="The versioned Parquet event store.")
_MANIFEST = typer.Option(DEFAULT_MANIFEST, help="The committed D1 universe manifest.")
_TOKENS = typer.Option(DEFAULT_TOKENS, help="The D1 symbol-to-token table.")
_DAY = typer.Option(None, formats=["%Y-%m-%d"], help="Print this morning.")


def _feed(name: str) -> CueFeed:
    if name not in FEEDS:
        raise ValueError(f"cue source {name!r}: choose one of {', '.join(FEEDS)}")
    return FEEDS[name]


async def _collect(
    raw: Path, ledger: Path, first: date, last: date, gap: float, sleeper: Sleeper, feed: CueFeed
) -> list[str]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        collector = CueCollector(
            PoliteGet(client, sleeper, gap), raw, FetchLedger(ledger), SystemClock(), feed
        )
        return await collector.run(CUES, first, last, typer.echo)


def research_collect_global_cues(
    raw: Path = _RAW,
    ledger: Path = _LEDGER,
    first: datetime = _FROM,
    last: datetime = _TO,
    seconds_between_requests: float = _GAP,
    source: str = _SOURCE,
) -> None:
    """Collect the five global-cue series (S&P 500, Nasdaq, USD/INR, Brent, US 10y)."""
    try:
        failed = asyncio.run(
            _collect(raw, ledger, first.date(), last.date(), seconds_between_requests,
                     AsyncioSleeper(), _feed(source))
        )  # fmt: skip
    except SourceRefused as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except CollectionHalted as error:
        typer.secho(f"halted: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"collect-global-cues failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    if failed:
        typer.echo(f"re-run to retry: {'; '.join(failed)}")
        raise typer.Exit(code=2)


D1_CONSTITUENTS = (
    Path("config/universe/d1/nifty100.csv"), Path("config/universe/d1/niftymidcap150.csv"),
)  # fmt: skip


def _committed_sectors() -> SectorMap:
    """The industry -> sector index map from the committed files (empty if run outside the repo)."""
    if not DEFAULT_SECTOR_TABLE.exists():
        return SectorMap({})
    return SectorMapLoader().load([p for p in D1_CONSTITUENTS if p.exists()])


def build_context_builder(
    loader: BarLoader, first: date, last: date, fo_dir: Path = DEFAULT_FO_STOCK_DIR,
    sectors: SectorMap | None = None, capacity: int = 64,
) -> AsOfContextBuilder:  # fmt: skip
    """The per-event context over real bars, with F&O open interest from the stock bhavcopy.
    `capacity` is how many names' bars are held: a replay walks every name in time order."""
    series = BarSeriesCache(loader, first, last, capacity=capacity)
    return AsOfContextBuilder(
        series,
        NIFTY_ID,
        VIX_ID,
        sectors if sectors is not None else _committed_sectors(),
        FoOpenInterest(FoDayStore(fo_dir)),
    )


def build_posture_inputs(
    loader: BarLoader, store: EventStore, cues_dir: Path, feed: CueFeed,
    names: dict[str, str], first: date, last: date, closes_file: Path = DEFAULT_CLOSES_FILE,
) -> NumericPostureInputs:  # fmt: skip
    """The posture inputs over real bars, real events and the collected cues."""
    series = BarSeriesCache(loader, first, last, capacity=4)
    ids = sorted(names.values())
    table = SessionCloseTable.open(closes_file, first, last, ids)
    if table is None:
        table = SessionCloseTable.load(loader, ids, first, last)
        table.save(closes_file, first, last)
    sections: list[PostureSection] = [
        IndexState(series, NIFTY_ID, VIX_ID),
        BreadthSection(Breadth(table)),
        GlobalCuesSection(GlobalCues.load(cues_dir, first, last, feed)),
        FilingCounts(store),
    ]
    return NumericPostureInputs(sections, series, NIFTY_ID)


def research_posture_inputs(
    manifest: Path = _MANIFEST,
    tokens: Path = _TOKENS,
    events: Path = _EVENTS,
    cues: Path = _RAW,
    source: str = _SOURCE,
    first: datetime = _FROM,
    last: datetime = _TO,
    day: datetime | None = _DAY,
) -> None:
    """Print one morning's posture numbers, or (no --day) how often each key is present."""
    try:
        loader = VaultedIntradayBars.from_settings(Settings.default())
        names = research_symbols(manifest, tokens)
        inputs = build_posture_inputs(
            loader, ParquetEventStore(events), cues, _feed(source), names, first.date(), last.date()
        )
        if day is not None:
            item = inputs.for_day(day.date())
            typer.echo(f"decision_at {item.decision_at.isoformat()}")
            for key, value in sorted(item.state.items()):
                typer.echo(f"  {key}: {value}")
            return
        sessions = _sessions(loader, first.date(), last.date())
        present: Counter[str] = Counter()
        for morning in sessions:
            present.update(inputs.for_day(morning).state.keys())
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"posture-inputs failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"{len(sessions)} mornings; key presence:")
    for key, n in sorted(present.items()):
        typer.echo(f"  {key:44s} {n:4d}  {100 * n / len(sessions):5.1f}%")


def _sessions(loader: BarLoader, first: date, last: date) -> list[date]:
    days = {b.ts.astimezone(IST).date() for b in loader.load(NIFTY_ID, first, last)}
    return sorted(d for d in days if first <= d <= last)
