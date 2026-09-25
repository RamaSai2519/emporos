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

from emporos.cli.llm_composition import DevData, LlmStack, declared_prices
from emporos.cli.posture_commands import build_posture_inputs
from emporos.core.clock import IST, SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.eventtrader.events import MarketEvent
from emporos.eventtrader.posture import PosturePlanner
from emporos.eventtrader.replay.report import (
    LlmLedger,
    VariantIdentity,
    render_report,
    write_daily_csv,
    write_trades_csv,
)
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
from emporos.eventtrader.stages.stages import TriageStage
from emporos.eventtrader.variants import VariantSpec, variant
from emporos.jev.prompts import JevPrompt
from emporos.research.filings.event_store import DEFAULT_EVENT_DIR, ParquetEventStore
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.fo_stock_archive import DATASET
from emporos.research.market_context.global_cues import DEFAULT_CUES_RAW_DIR

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


def _estimate_line(name: str, stack: LlmStack, data: DevData, named: list[MarketEvent]) -> str:
    from emporos.eventtrader.replay.engine import decision_input

    stage = TriageStage(stack.mini(), "x")  # only its renderer is used
    chars = 0
    for event in named:
        user = json.dumps(stage.render(decision_input(event, data.context)), sort_keys=True)
        chars += len(TRIAGE_V1.system_text) + len(user)
    tokens_in = chars // CHARS_PER_TOKEN
    tokens_out = len(named) * TRIAGE_OUTPUT_TOKENS
    prices = declared_prices()
    mini = next(iter(prices.prices))
    usd = prices.cost_usd(mini, tokens_in, tokens_out)
    per_event = usd / len(named) if named else Decimal(0)
    later = per_event * LATER_STAGE_CALLS
    return (
        f"Dev {name}: {len(named)} triage calls, ~{tokens_in / 1e6:.1f}M tokens in and "
        f"~{tokens_out / 1e6:.1f}M out, about USD {usd:.2f} at the declared mini prices; each "
        f"event passing triage adds {LATER_STAGE_CALLS} calls (about USD {later:.4f} at triage's "
        "size); a repeat run is answered from the journal"
    )


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
_CEILING = typer.Option(25.0, help="A runaway guard: a hard USD ceiling on mini's calls.")
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
) -> None:  # fmt: skip
    if last > LAST_DAY or first < FIRST_DAY:
        raise ValueError("Dev is 2024-01-01..2024-12-31: the vault seals what is after")
    store = FoDayStore(stock_fo)
    if not no_options and not any(store.days()):
        raise ValueError(f"no stock F&O files under {stock_fo}; fetch first or pass --no-options")
    data = DevData(Settings.default(), first, last, None if no_options else store)
    stack = LlmStack(
        Settings.default(), journal, declared_prices(), record=not (replay_only or estimate_only),
        mini_ceiling_usd=ceiling,
    )  # fmt: skip
    events = _window(first, last, ParquetEventStore(events_dir))
    named = [e for e in events if e.instrument_id in data.instrument_ids]
    typer.echo(
        f"events {len(events)}; skipped {len(events) - len(named)} with no instrument or outside "
        f"D1 research names; snapshot {snapshot} = {events_digest(events)}"
    )
    if estimate_only:
        typer.echo(_estimate_line(spec.name, stack, data, named))
        return
    identity = VariantIdentity(
        HYPOTHESIS, spec.name, first, last, prompts_hash(), ("openai/gpt-4o-mini",),
        f"{snapshot}:{events_digest(events)}",
    )  # fmt: skip
    schedule = None
    if spec.posture:
        typer.echo("building the posture inputs (numbers as known at 09:00 each morning)")
        inputs = build_posture_inputs(
            data.bars, ParquetEventStore(events_dir), cues, data.symbols, first, last
        )
        planner = PosturePlanner(
            stack.posture_stage(), inputs, stack.scopes, declared_prices(), concurrency
        )
        schedule = await planner.plan(data.sessions)
        typer.echo(f"posture: {dict(schedule.counts())}, {len(schedule.failures)} failed (HOLD)")
    runner = VariantRunner(
        identity, stack.pipeline(spec), data.engines(), data.context, stack.scopes,
        declared_prices(), data.sessions, spec.triage_threshold, schedule, concurrency,
        control_runs,
        calls=stack,
    )  # fmt: skip
    outcome = await runner.run(named)
    report = outcome.report
    files = {
        "report": str(reports / f"{spec.name}-report.txt"),
        "trades": str(reports / f"{spec.name}-trades.csv"),
        "daily": str(reports / f"{spec.name}-daily.csv"),
    }
    reports.mkdir(parents=True, exist_ok=True)
    text = render_report(report)
    Path(files["report"]).write_text(text, encoding="utf-8")
    write_trades_csv(Path(files["trades"]), report.result.trades)
    write_daily_csv(Path(files["daily"]), report.benchmark, report.adverse)
    added = LlmLedger(ledger).append(report, SystemClock().now(), files)
    typer.echo(text)
    typer.echo(f"ledger: {'one look added' if added else 'already counted'} ({ledger})")
