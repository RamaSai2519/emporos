"""`emporos research collect-filings`: Track L's corporate announcements (EM-239, §12.2).

Asks the exchange's public announcements feed, one polite request at a time, for every name in the
Track L universe (the D1 research names plus the F&O stock list, never the held-out D1 names) and
every calendar-year window of 2024-01-01..2026-03-18. Each reply is kept verbatim outside git and
each fetch is recorded, with its URL and date, in `docs/research/profit/filings-ledger.jsonl`. A
window already in the ledger is skipped, so an interrupted run resumes; a refusal (401, 403, 429)
stops the run. Run from the development machine only."""

from __future__ import annotations

import asyncio
import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import typer

from emporos.cli.corporate_actions_commands import DEFAULT_TOKENS, research_symbols
from emporos.cli.intraday_bars import VaultedIntradayBars
from emporos.core.clock import IST, AsyncioSleeper, Sleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.research.d1_universe import DEFAULT_MANIFEST, D1Manifest
from emporos.research.filings.attachments import (
    DEFAULT_TEXT_DIR,
    AttachmentCollector,
    AttachmentHalted,
    AttachmentText,
    AttachmentTextStore,
)
from emporos.research.filings.collector import (
    CollectionHalted,
    CollectionReport,
    FilingCollector,
    yearly_windows,
)
from emporos.research.filings.event_store import (
    DEFAULT_EVENT_DIR,
    EventBuilder,
    EventRow,
    ParquetEventStore,
)
from emporos.research.filings.loading import CollectedFilings
from emporos.research.filings.nse_source import NseFilingSource
from emporos.research.filings.pdf_text import PdftotextExtractor
from emporos.research.filings.polite import PoliteGet, SourceRefused
from emporos.research.filings.priority import AttachmentPriority
from emporos.research.filings.raw_store import (
    DEFAULT_FILINGS_LEDGER,
    DEFAULT_RAW_DIR,
    FetchLedger,
    RawFilingStore,
)
from emporos.research.filings.report import filing_report_lines
from emporos.research.filings.snapshot import DEFAULT_SNAPSHOT_DIR, EventSnapshot
from emporos.research.filings.universe import FilingUniverse
from emporos.research.market_context.coverage import BarCoverage, FoCoverage, coverage_lines

DEFAULT_FO_FILE = Path("config/universe/track-l/fo_mktlots.csv")
FIRST_DAY = datetime(2024, 1, 1)
LAST_DAY = datetime(2026, 3, 18)

_MANIFEST = typer.Option(DEFAULT_MANIFEST, help="The committed D1 universe manifest.")
_TOKENS = typer.Option(DEFAULT_TOKENS, help="The D1 symbol-to-token table.")
_FO_FILE = typer.Option(DEFAULT_FO_FILE, help="The committed NSE F&O market-lot file.")
_RAW = typer.Option(DEFAULT_RAW_DIR, help="Where each reply is kept verbatim (local, not git).")
_LEDGER = typer.Option(DEFAULT_FILINGS_LEDGER, help="The append-only fetch ledger.")
_FROM = typer.Option(FIRST_DAY, formats=["%Y-%m-%d"], help="First day to ask for.")
_TO = typer.Option(LAST_DAY, "--to", formats=["%Y-%m-%d"], help="Last day (in).")
_GAP = typer.Option(3.0, help="Seconds between requests (at least 1).")
_ONLY = typer.Option("", help="Comma-separated symbols: only these (for a probe or a retry).")


def track_l_universe(manifest: Path, tokens: Path, fo_file: Path) -> FilingUniverse:
    with tokens.open(encoding="utf-8", newline="") as handle:
        token_symbols = {f"NSE:{row['Token']}": row["Symbol"] for row in csv.DictReader(handle)}
    return FilingUniverse.load(
        research_symbols(manifest, tokens),
        frozenset(D1Manifest.load(manifest).holdout),
        token_symbols,
        fo_file,
    )


async def _collect(
    symbols: list[str], raw: Path, ledger: Path, first: date, last: date, gap: float,
    sleeper: Sleeper,
) -> CollectionReport:  # fmt: skip
    async with httpx.AsyncClient(timeout=60.0) as client:
        source = NseFilingSource(PoliteGet(client, sleeper, gap))
        collector = FilingCollector(source, RawFilingStore(raw), FetchLedger(ledger), SystemClock())
        return await collector.run(symbols, yearly_windows(first, last), typer.echo)


def research_collect_filings(
    manifest: Path = _MANIFEST,
    tokens: Path = _TOKENS,
    fo_file: Path = _FO_FILE,
    raw: Path = _RAW,
    ledger: Path = _LEDGER,
    first: datetime = _FROM,
    last: datetime = _TO,
    seconds_between_requests: float = _GAP,
    only: str = _ONLY,
) -> None:
    """Collect NSE corporate announcements for the Track L universe."""
    try:
        universe = track_l_universe(manifest, tokens, fo_file)
        symbols = [s for s in universe.symbols if not only or s in only.split(",")]
        typer.echo(
            f"{len(symbols)} names ({len(universe.d1)} D1, {len(universe.fo_stocks)} F&O, "
            f"{len(universe.held_out)} held-out D1 names never asked about)"
        )
        report = asyncio.run(
            _collect(symbols, raw, ledger, first.date(), last.date(), seconds_between_requests,
                     AsyncioSleeper())
        )  # fmt: skip
    except SourceRefused as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except CollectionHalted as error:
        typer.secho(f"halted: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"collect-filings failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{report.fetched} window(s) fetched, {report.skipped} already held, "
        f"{report.filings} filing(s); {len(report.failed)} failed"
    )
    if report.failed:
        typer.echo(f"re-run to retry: {', '.join(report.failed)}")
        raise typer.Exit(code=2)


_TEXT = typer.Option(DEFAULT_TEXT_DIR, help="Where extracted attachment text is kept (local).")
_EVENTS = typer.Option(DEFAULT_EVENT_DIR, help="The versioned Parquet event store.")
_LIMIT = typer.Option(0, help="Fetch at most this many attachments this run (0: no limit).")
_ROUTINE = typer.Option(False, help="Also fetch the routine categories (tier 3).")


async def _extract(
    urls: list[str], text: Path, limit: int | None, gap: float, sleeper: Sleeper
) -> tuple[int, dict[str, int]]:
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=False) as client:
        collector = AttachmentCollector(
            PoliteGet(client, sleeper, gap),
            PdftotextExtractor(),
            AttachmentTextStore(text),
            SystemClock(),
        )
        report = await collector.run(urls, limit, typer.echo)
    return report.fetched, report.by_status


def research_extract_filing_text(
    raw: Path = _RAW,
    ledger: Path = _LEDGER,
    text: Path = _TEXT,
    seconds_between_requests: float = _GAP,
    limit: int = _LIMIT,
    include_routine: bool = _ROUTINE,
) -> None:
    """Fetch the filings' PDF attachments in priority order and extract their text locally."""
    try:
        filings = list(CollectedFilings(RawFilingStore(raw), FetchLedger(ledger)))
        urls = AttachmentPriority(include_routine).order(filings)
        held = len(AttachmentTextStore(text).urls())
        typer.echo(f"{len(urls)} attachments in scope, {held} already held")
        fetched, by_status = asyncio.run(
            _extract(urls, text, limit or None, seconds_between_requests, AsyncioSleeper())
        )
    except SourceRefused as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except AttachmentHalted as error:
        typer.secho(f"halted: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"extract-filing-text failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"{fetched} attachment(s) fetched this run: {by_status}")


def _instrument_ids(tokens: Path) -> dict[str, str]:
    with tokens.open(encoding="utf-8", newline="") as handle:
        return {row["Symbol"]: f"NSE:{row['Token']}" for row in csv.DictReader(handle)}


def _event_rows(
    tokens: Path, raw: Path, ledger: Path, text: Path
) -> tuple[list[EventRow], dict[str, AttachmentText]]:
    filings = CollectedFilings(RawFilingStore(raw), FetchLedger(ledger))
    texts = {a.url: a for a in AttachmentTextStore(text).all()}
    return EventBuilder(_instrument_ids(tokens)).build(filings, texts), texts


def research_build_events(
    tokens: Path = _TOKENS,
    raw: Path = _RAW,
    ledger: Path = _LEDGER,
    text: Path = _TEXT,
    events: Path = _EVENTS,
) -> None:
    """Build the versioned event store from the collected filings and the extracted text."""
    try:
        rows, texts = _event_rows(tokens, raw, ledger, text)
        months = ParquetEventStore(events).write(
            rows, SystemClock().now(), {"ledger": str(ledger), "attachments_held": len(texts)}
        )
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"build-events failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"{len(rows)} events in {months} month file(s) under {events}")


_NAME = typer.Argument(help="The snapshot's name, e.g. dev-2024 (an existing name is refused).")
_SNAPSHOTS = typer.Option(DEFAULT_SNAPSHOT_DIR, help="Where frozen snapshots live.")


def research_freeze_events(
    name: str = _NAME,
    tokens: Path = _TOKENS,
    raw: Path = _RAW,
    ledger: Path = _LEDGER,
    text: Path = _TEXT,
    snapshots: Path = _SNAPSHOTS,
) -> None:
    """Freeze the events as they are now (with the PDF text extracted so far) for a Dev run."""
    try:
        rows, texts = _event_rows(tokens, raw, ledger, text)
        snapshot = EventSnapshot(snapshots / name)
        record = snapshot.freeze(
            rows, SystemClock().now(), {"ledger": str(ledger), "attachments_held": len(texts)}
        )
        verified = snapshot.verify()
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"freeze-events failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"snapshot {snapshot.root}: {record['events']} events, {record['text_status']}")
    typer.echo(f"attachments held {record['attachments_held']}; files verified: {verified}")


def research_filings_report(
    events: Path = _EVENTS,
    text: Path = _TEXT,
    first: datetime = _FROM,
    last: datetime = _TO,
) -> None:
    """The event store in numbers: per month, category mix, delays, image-only share."""
    store = ParquetEventStore(events)
    end = datetime.combine(last.date() + timedelta(days=1), datetime.min.time(), tzinfo=IST)
    rows = store.rows_between(datetime.combine(first.date(), datetime.min.time(), tzinfo=IST), end)
    texts = {a.url: a for a in AttachmentTextStore(text).all()}
    for line in filing_report_lines(rows, texts):
        typer.echo(line)


_D1_FIRST = typer.Option(datetime(2024, 1, 1), formats=["%Y-%m-%d"], help="First day.")
_FO_LEDGER = typer.Option(Path("docs/research/profit/fo-archive-ledger.jsonl"), help="F&O ledger.")
NIFTY_ID = "NSE:99926000"


def research_track_l_coverage(
    manifest: Path = _MANIFEST,
    tokens: Path = _TOKENS,
    fo_ledger: Path = _FO_LEDGER,
    first: datetime = _D1_FIRST,
    last: datetime = _TO,
) -> None:
    """Report the 5-minute bar coverage of the D1 names and the F&O bhavcopy coverage."""
    try:
        symbols = research_symbols(manifest, tokens)
        loader = VaultedIntradayBars.from_settings(Settings.default())
        coverage = BarCoverage(loader, NIFTY_ID)
        names = coverage.of({i: s for s, i in symbols.items()}, first.date(), last.date())
        expected = coverage.expected_sessions(first.date(), last.date())
        fo = FoCoverage.of(fo_ledger, expected, first.date(), last.date())
    except (EmporosError, ValueError, OSError, KeyError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"track-l-coverage failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in coverage_lines(names, len(expected), fo, first.date(), last.date()):
        typer.echo(line)
