"""`emporos research collect-actions` and `build-adjustments`: the action ledger (EM-221).

`collect-actions` asks the exchange's public corporate-actions feed, one polite request per D1
research name, for every action in the window, and appends each record with its source URL and fetch
date to `docs/research/profit/corporate-actions.jsonl`. A name already collected is skipped, so an
interrupted run resumes; a refusal stops the run. `build-adjustments` turns the recorded splits and
bonuses into the factor ledger (`config/universe/d1/adjustments.yaml`) and lists what it did NOT
adjust. No prices are read by either. The held-out names are never asked about."""

from __future__ import annotations

import asyncio
import csv
from datetime import date, datetime
from pathlib import Path

import httpx
import typer
import yaml

from emporos.core.clock import AsyncioSleeper, Clock, Sleeper, SystemClock
from emporos.core.errors import EmporosError
from emporos.research.adjustments import DEFAULT_LEDGER
from emporos.research.corporate_actions import CorporateActionLedger, FactorBuilder
from emporos.research.d1_universe import DEFAULT_MANIFEST, D1Manifest
from emporos.research.nse_corporate_actions import (
    ActionsRefused,
    CorporateActionSource,
    NseCorporateActionSource,
)

PROFIT_DIR = Path("docs/research/profit")
DEFAULT_TOKENS = Path("config/universe/d1/tokens.csv")
DEFAULT_REVIEW = PROFIT_DIR / "corporate-actions-unadjusted.yaml"

_MANIFEST = typer.Option(DEFAULT_MANIFEST, help="The committed D1 universe manifest.")
_TOKENS = typer.Option(DEFAULT_TOKENS, help="The D1 symbol-to-token table.")
_OUT = typer.Option(PROFIT_DIR, help="Where the append-only action ledger lives.")
_FROM = typer.Option(datetime(2016, 10, 3), formats=["%Y-%m-%d"], help="First ex-date to ask for.")
_TO = typer.Option(datetime(2026, 3, 18), "--to", formats=["%Y-%m-%d"], help="Last ex-date (in).")
_GAP = typer.Option(3.0, help="Seconds between requests (at least 1).")
_LEDGER_OUT = typer.Option(DEFAULT_LEDGER, help="The factor ledger to write.")
_REVIEW_OUT = typer.Option(DEFAULT_REVIEW, help="Price-affecting actions NOT adjusted.")


def research_symbols(manifest: Path, tokens: Path) -> dict[str, str]:
    """symbol -> instrument id for the manifest's `included` names only."""
    included = set(D1Manifest.load(manifest).included)
    with tokens.open(encoding="utf-8", newline="") as handle:
        rows = {row["Symbol"]: f"NSE:{row['Token']}" for row in csv.DictReader(handle)}
    return {symbol: i for symbol, i in rows.items() if i in included}


class ActionCollection:
    """Collect, name by name, what the ledger does not hold yet."""

    def __init__(
        self, source: CorporateActionSource, ledger: CorporateActionLedger, clock: Clock
    ) -> None:
        self._source = source
        self._ledger = ledger
        self._clock = clock

    async def run(self, symbols: list[str], first: date, last: date) -> tuple[int, list[str]]:
        """(actions added, symbols that failed). A refusal from the exchange propagates."""
        added, failed = 0, []
        done = self._ledger.collected_symbols()
        for symbol in symbols:
            if symbol in done:
                continue
            try:
                actions = await self._source.actions(symbol, first, last)
            except ActionsRefused:
                raise
            except (httpx.HTTPError, ValueError) as error:
                failed.append(symbol)
                typer.echo(f"{symbol}: failed ({error})")
                continue
            url = self._source.url_for(symbol, first, last)
            new = self._ledger.record(symbol, actions, first, last, self._clock.now(), url)
            added += new
            typer.echo(f"{symbol}: {len(actions)} action(s), {new} new")
        return added, failed


async def _collect(
    symbols: list[str], out: Path, first: date, last: date, gap: float, sleeper: Sleeper
) -> tuple[int, list[str]]:
    ledger = CorporateActionLedger(
        out / "corporate-actions.jsonl", out / "corporate-actions-collected.jsonl"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        source = NseCorporateActionSource(client, sleeper, gap)
        return await ActionCollection(source, ledger, SystemClock()).run(symbols, first, last)


def research_collect_actions(
    manifest: Path = _MANIFEST,
    tokens: Path = _TOKENS,
    out: Path = _OUT,
    first: datetime = _FROM,
    last: datetime = _TO,
    seconds_between_requests: float = _GAP,
) -> None:
    """Collect corporate actions for the D1 research names from the exchange's public feed."""
    try:
        symbols = sorted(research_symbols(manifest, tokens))
        added, failed = asyncio.run(
            _collect(symbols, out, first.date(), last.date(), seconds_between_requests,
                     AsyncioSleeper())
        )  # fmt: skip
    except ActionsRefused as error:
        typer.secho(f"stopped: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from error
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"collect-actions failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(f"{added} new action(s); {len(failed)} name(s) failed")
    if failed:
        typer.echo(f"re-run to retry: {', '.join(failed)}")
        raise typer.Exit(code=2)


def research_build_adjustments(
    manifest: Path = _MANIFEST,
    tokens: Path = _TOKENS,
    actions: Path = _OUT,
    out: Path = _LEDGER_OUT,
    review: Path = _REVIEW_OUT,
) -> None:
    """Turn the recorded splits and bonuses into adjustment factors; list what is left over."""
    try:
        ledger = CorporateActionLedger(
            actions / "corporate-actions.jsonl", actions / "corporate-actions-collected.jsonl"
        )
        source = "NSE corporate-actions feed (nseindia.com/api/corporates-corporateActions)"
        built = FactorBuilder().build(ledger.actions(), research_symbols(manifest, tokens), source)
        built.ledger.save(out)
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text(
            yaml.safe_dump(
                {
                    "note": "Price-affecting actions left UNADJUSTED: the text gave no ratio.",
                    "actions": [
                        {"symbol": a.symbol, "ex_date": a.ex_date.isoformat(), "subject": a.subject}
                        for a in built.review
                    ],
                },
                sort_keys=False,
                width=120,
            ),
            encoding="utf-8",
        )
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"build-adjustments failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"{len(built.ledger)} factor(s) for {len(built.ledger.instrument_ids)} name(s); "
        f"{len(built.review)} price-affecting action(s) not adjusted; "
        f"{built.ignored} cash action(s) ignored; ledger {built.ledger.content_hash[:12]}"
    )
