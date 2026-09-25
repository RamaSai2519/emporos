"""`emporos research run-llm-variant` — one Track L Dev variant (EM-240, PROFIT_PLAN §12).

Offline research: no broker, no order, no database. It calls the LLM gateway (record mode: every
real call is journalled, a repeat run is free) unless `--replay-only`. `--estimate-only` builds the
prompts and prints what a run would ask for, with no call."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import typer

from emporos.cli.llm_composition import DAILY_TOKEN_CAP, DevData, LlmStack, declared_prices
from emporos.cli.posture_commands import build_posture_inputs
from emporos.core.clock import IST, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.eventtrader.estimate import estimate_run
from emporos.eventtrader.events import MarketEvent
from emporos.eventtrader.llm.http_clients import MINI_MODEL
from emporos.eventtrader.posture import PosturePlanner
from emporos.eventtrader.prefilter import CategoryPreFilter
from emporos.eventtrader.replay.report import (
    LlmLedger,
    VariantIdentity,
    render_report,
    write_daily_csv,
    write_trades_csv,
)
from emporos.eventtrader.replay.table import load_summaries, render_table, summary_of
from emporos.eventtrader.runner import RunIncomplete, VariantRunner, events_digest
from emporos.eventtrader.stages.prompts import (
    ARBITER_V1,
    BEAR_V1,
    BULL_V1,
    JUDGE_V1,
    POSTURE_V2,
    TAPE_V1,
    TRIAGE_V1,
)
from emporos.eventtrader.variants import VariantSpec, variant
from emporos.jev.prompts import JevPrompt
from emporos.research.filings.event_store import DEFAULT_EVENT_DIR, ParquetEventStore
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.fo_stock_archive import DATASET
from emporos.research.market_context.global_cues import DEFAULT_CUES_RAW_DIR, FEEDS

DEFAULT_JOURNAL = Path.home() / ".cache" / "emporos" / "llm" / "l1-journal.jsonl"
DEFAULT_STOCK_FO = Path.home() / ".cache" / "emporos" / DATASET
DEFAULT_REPORTS = Path("docs/research/profit/reports/l1")
DEFAULT_LEDGER = Path("docs/research/profit/screens.jsonl")
HYPOTHESIS = "l1-llm-event-trader"
FIRST_DAY, LAST_DAY = date(2024, 1, 1), date(2024, 12, 31)  # Dev
CHARS_PER_TOKEN = 4  # a rough figure for the estimate only
TRIAGE_OUTPUT_TOKENS = 80
LATER_STAGE_CALLS = 4  # bull, bear, tape, judge

_PROMPTS: tuple[JevPrompt, ...] = (
    TRIAGE_V1, BULL_V1, BEAR_V1, TAPE_V1, JUDGE_V1, ARBITER_V1, POSTURE_V2,
)  # fmt: skip


def prompts_hash() -> str:
    import hashlib

    joined = "\x1f".join(p.content_hash for p in _PROMPTS)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _window(first: date, last: date, store: ParquetEventStore) -> list[MarketEvent]:
    start = datetime.combine(first, datetime.min.time(), tzinfo=IST)
    end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=IST)
    return list(store.events_between(start, end))


PASS_RATES = (0.10, 0.20, 0.30)  # the share of events triage passes on: unknown until asked


def _estimate_lines(stack: LlmStack, data: DevData, kept: list[MarketEvent]) -> list[str]:
    from emporos.eventtrader.replay.engine import decision_input

    items = [decision_input(event, data.context) for event in kept]
    lines = []
    for e in estimate_run(items, len(data.sessions), PASS_RATES, declared_prices(), MINI_MODEL):
        lines.append(f"if triage passes {e.pass_rate:.0%} of {len(items)} events (v1-v5 together):")
        lines += [
            f"  {s.name:<36} {s.calls:>7} calls {s.tokens_in / 1e6:>6.2f}M in "
            f"{s.tokens_out / 1e6:>5.2f}M out  USD {s.usd:.3f}"
            for s in e.stages
        ]
        lines.append(
            f"  TOTAL {e.calls} calls, USD {e.usd:.2f} on normal calls, USD {e.usd_batch:.2f} "
            f"through the Batch API (half price); ceiling USD 3.00, "
            f"{'WITHIN' if e.usd <= 3 else 'OVER'} on normal calls"
        )
    return lines


_VARIANT = typer.Argument(..., help="v1_t60, v2_t75, v3_nopanel_t60, ...")
_FIRST = typer.Option(datetime(2024, 1, 1), "--first", formats=["%Y-%m-%d"], help="First day.")
_LAST = typer.Option(datetime(2024, 12, 31), "--last", formats=["%Y-%m-%d"], help="Last day.")
_EVENTS = typer.Option(DEFAULT_EVENT_DIR, help="The FROZEN events snapshot.")
_SNAPSHOT = typer.Option(..., help="The snapshot's id (goes into the report).")
_STOCK_FO = typer.Option(DEFAULT_STOCK_FO, help="The stock F&O dataset.")
_JOURNAL = typer.Option(DEFAULT_JOURNAL, help="The call journal (local, not git).")
_REPORTS = typer.Option(DEFAULT_REPORTS, help="Where the report and CSVs go.")
_LEDGER = typer.Option(DEFAULT_LEDGER, help="The counted-looks ledger.")
_CONCURRENCY = typer.Option(8, min=1, help="Calls in flight.")
_CEILING = typer.Option(
    25.0, help="A runaway guard: a USD ceiling at list price on ALL Dev mini calls, across runs."
)
_DAILY = typer.Option(DAILY_TOKEN_CAP, min=1, help="Tokens per UTC day; pauses past it.")
_RUNS = typer.Option(1000, min=1, help="Coin-flip control runs.")
_REPLAY = typer.Option(False, help="Answer only from the journal; never call.")
_ESTIMATE = typer.Option(False, help="Print the size of the run and stop.")
_NO_OPTIONS = typer.Option(False, help="Run without the stock option chains.")
_CUES = typer.Option(DEFAULT_CUES_RAW_DIR, help="The collected global cues (may be empty).")


def research_run_llm_variant(
    variant_name: str = _VARIANT,
    first: datetime = _FIRST,
    last: datetime = _LAST,
    events_dir: Path = _EVENTS,
    snapshot: str = _SNAPSHOT,
    stock_fo: Path = _STOCK_FO,
    journal: Path = _JOURNAL,
    reports: Path = _REPORTS,
    ledger: Path = _LEDGER,
    concurrency: int = _CONCURRENCY,
    mini_ceiling_usd: float = _CEILING,
    control_runs: int = _RUNS,
    replay_only: bool = _REPLAY,
    estimate_only: bool = _ESTIMATE,
    no_options: bool = _NO_OPTIONS,
    cues: Path = _CUES,
    daily_token_cap: int = _DAILY,
) -> None:
    """Run one Dev variant: decide, replay, report; one counted look."""
    try:
        spec = variant(variant_name)
        asyncio.run(
            _run(
                spec,
                first.date(),
                last.date(),
                events_dir,
                snapshot,
                stock_fo,
                journal,
                reports,
                ledger,
                concurrency,
                Decimal(str(mini_ceiling_usd)),
                control_runs,
                replay_only,
                estimate_only,
                no_options,
                cues,
                daily_token_cap,
            )  # fmt: skip
        )
    except (EmporosError, ValueError, OSError, KeyError, RunIncomplete) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"run-llm-variant failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error


async def _run(
    spec: VariantSpec, first: date, last: date, events_dir: Path, snapshot: str, stock_fo: Path,
    journal: Path, reports: Path, ledger: Path, concurrency: int, ceiling: Decimal,
    control_runs: int, replay_only: bool, estimate_only: bool, no_options: bool, cues: Path,
    daily_token_cap: int,
) -> None:  # fmt: skip
    if last > LAST_DAY or first < FIRST_DAY:
        raise ValueError("Dev is 2024-01-01..2024-12-31: the vault seals what is after")
    store = FoDayStore(stock_fo)
    if not no_options and not any(store.days()):
        raise ValueError(f"no stock F&O files under {stock_fo}; fetch first or pass --no-options")
    data = DevData(Settings.default(), first, last, stock_fo, chains=not no_options)
    stack = LlmStack(
        Settings.default(), journal, declared_prices(), record=not (replay_only or estimate_only),
        mini_ceiling_usd=ceiling, daily_token_cap=daily_token_cap, log=typer.echo,
    )  # fmt: skip
    events = _window(first, last, ParquetEventStore(events_dir))
    named = [e for e in events if e.instrument_id in data.instrument_ids]
    kept, dropped = CategoryPreFilter().split(named)
    typer.echo(
        f"events {len(events)}; skipped {len(events) - len(named)} with no instrument or outside "
        f"D1 research names; snapshot {snapshot} = {events_digest(events)}"
    )
    typer.echo(
        f"pre-filter drops {sum(dropped.values())} routine filings before triage "
        f"({dict(dropped.most_common())}); {len(kept)} events go on"
    )
    if estimate_only:
        for line in _estimate_lines(stack, data, kept):
            typer.echo(line)
        return
    identity = VariantIdentity(
        HYPOTHESIS, spec.name, first, last, prompts_hash(), (MINI_MODEL,),
        f"{snapshot}:{events_digest(events)}",
    )  # fmt: skip
    schedule = None
    if spec.posture:
        typer.echo("building the posture inputs (numbers as known at 09:00 each morning)")
        inputs = build_posture_inputs(
            data.bars,
            ParquetEventStore(events_dir),
            cues,
            FEEDS["yahoo"],
            data.symbols,
            first,
            last,
        )
        planner = PosturePlanner(
            stack.posture_stage(), inputs, stack.scopes, declared_prices(), concurrency
        )
        schedule = await planner.plan(data.sessions)
        typer.echo(f"posture: {dict(schedule.counts())}, {len(schedule.failures)} failed (HOLD)")
    runner = VariantRunner(
        identity, stack.pipeline(spec), data.engines(), data.context, stack.scopes,
        declared_prices(), data.sessions, spec.triage_threshold, schedule, concurrency,
        control_runs, calls=stack, prefiltered=dict(dropped),
    )  # fmt: skip
    outcome = await runner.run(kept)
    report = outcome.report
    files = {
        "report": str(reports / f"{spec.name}-report.txt"),
        "trades": str(reports / f"{spec.name}-trades.csv"),
        "daily": str(reports / f"{spec.name}-daily.csv"),
        "summary": str(reports / f"{spec.name}-summary.json"),
    }
    reports.mkdir(parents=True, exist_ok=True)
    text = render_report(report)
    Path(files["report"]).write_text(text, encoding="utf-8")
    write_trades_csv(Path(files["trades"]), report.result.trades)
    write_daily_csv(Path(files["daily"]), report.benchmark, report.adverse)
    summary = {**summary_of(report), "tokens_by_day": stack.token_usage()}
    Path(files["summary"]).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    added = LlmLedger(ledger).append(report, SystemClock().now(), files)
    typer.echo(text)
    typer.echo(f"tokens by UTC day (input + output): {stack.token_usage()}")
    typer.echo(f"ledger: {'one look added' if added else 'already counted'} ({ledger})")


def research_llm_variant_table(reports: Path = _REPORTS) -> None:
    """The variants side by side, from the summaries their runs left in the reports directory."""
    typer.echo(render_table(load_summaries(reports)))
