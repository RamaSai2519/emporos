"""`emporos paper parity` — how paper trading compared with the backtest of the same config.

Read-only: it reads what paper recorded, replays it in a backtest, and stores/prints the
comparison. It needs no broker credentials and can place no order.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import typer

from emporos.cli.parity_composition import DEFAULT_PARITY_CASH, ParityRuntime, open_parity_runtime
from emporos.core.clock import IST
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.money import Money
from emporos.domain.parity import ParityKind, ParityReport
from emporos.parity.codec import SessionParityCodec
from emporos.parity.ledger import SessionParity
from emporos.parity.report import ParityDocument

paper_app = typer.Typer(help="Paper trading tools.", no_args_is_help=True)
parity_app = typer.Typer(help="Backtest-vs-paper parity reports.", no_args_is_help=True)
paper_app.add_typer(parity_app, name="parity")

_DATE = ["%Y-%m-%d"]
_DAY = typer.Option(None, "--date", formats=_DATE, help="IST session day (default: today).")
_CASH = typer.Option(DEFAULT_PARITY_CASH, help="The paper account's starting cash (quoted).")
_STRATEGY = typer.Option(..., "--strategy", "-s", help="Strategy name.")
_FROM = typer.Option(None, "--from", formats=_DATE, help="First session (inclusive).")
_TO = typer.Option(None, "--to", formats=_DATE, help="Last session (inclusive).")
_OUT = typer.Option(Path("docs/paper/parity"), help="Where to write the rendered reports.")


def _today() -> date:
    return datetime.now(IST).date()


def _fail(what: str, error: Exception) -> typer.Exit:
    message = error.message if isinstance(error, EmporosError) else str(error)
    typer.secho(f"{what} failed: {message}", fg=typer.colors.RED)
    return typer.Exit(code=1)


def _line(report: ParityReport) -> str:
    failing = "; ".join(g.name for g in report.failing)
    tail = f" — failing: {failing}" if failing else ""
    return (
        f"{report.strategy} {report.kind.value} {report.period}: {report.verdict.value.upper()} "
        f"({report.sessions} session(s), {report.matched_trades} matched trade(s)){tail}"
    )


async def _daily(day: date, cash: str) -> list[str]:
    async with open_parity_runtime(Settings.default(), Money.of(cash)) as runtime:
        outcome = await runtime.service.daily(day)
    lines = [_line(r) for r in (*outcome.reports, *outcome.rolled_up)]
    if outcome.already_reported:
        lines.append(f"{outcome.already_reported} run(s) already reported for {day}")
    lines += [f"skipped run {s.run_id}: {s.reason}" for s in outcome.skipped]
    return lines or [f"no paper runs recorded for {day}"]


@parity_app.command("daily")
def parity_daily(day: datetime | None = _DAY, cash: str = _CASH) -> None:
    """Compare a session with its shadow backtest and store the report (re-run to backfill)."""
    target = day.date() if day else _today()
    try:
        lines = asyncio.run(_daily(target, cash))
    except (EmporosError, ValueError) as error:
        raise _fail("parity daily", error) from error
    for line in lines:
        typer.echo(line)


async def _weekly(day: date) -> list[str]:
    async with open_parity_runtime(Settings.default()) as runtime:
        reports = await runtime.service.weekly(day)
    return [_line(r) for r in reports] or [f"no daily reports found in the week of {day}"]


@parity_app.command("weekly")
def parity_weekly(day: datetime | None = _DAY) -> None:
    """Build the ISO week's report from the stored dailies (no shadow backtest is run)."""
    target = day.date() if day else _today()
    try:
        lines = asyncio.run(_weekly(target))
    except (EmporosError, ValueError) as error:
        raise _fail("parity weekly", error) from error
    for line in lines:
        typer.echo(line)


async def _sessions(
    runtime: ParityRuntime, strategy: str, first: date | None, last: date | None
) -> tuple[list[SessionParity], list[ParityReport]]:
    codec = SessionParityCodec()
    sessions: list[SessionParity] = []
    reports = [r for r in await runtime.store.for_strategy(strategy)]
    for report in sorted(
        (r for r in reports if r.kind is ParityKind.DAILY), key=lambda r: r.first_session
    ):
        if first and report.first_session < first or last and report.first_session > last:
            continue
        sessions.append(codec.from_document(report.payload))
    return sessions, reports


async def _show(strategy: str, first: date | None, last: date | None) -> list[str]:
    async with open_parity_runtime(Settings.default()) as runtime:
        sessions, reports = await _sessions(runtime, strategy, first, last)
        if not sessions:
            return [f"no parity reports for {strategy}"]
        cumulative = runtime.service.assemble(sessions, ParityKind.CUMULATIVE)
    lines = [_line(cumulative), ""]
    lines += [f"  {g.outcome.upper():8} {g.name}: {g.detail}" for g in cumulative.gates]
    lines += ["", "stored reports:"] + [f"  {_line(r)}" for r in reports[:20]]
    return lines


@parity_app.command("show")
def parity_show(
    strategy: str = _STRATEGY, first: datetime | None = _FROM, last: datetime | None = _TO
) -> None:
    """Print the cumulative comparison over the stored sessions, and the newest stored reports."""
    try:
        lines = asyncio.run(
            _show(strategy, first.date() if first else None, last.date() if last else None)
        )
    except (EmporosError, ValueError) as error:
        raise _fail("parity show", error) from error
    for line in lines:
        typer.echo(line)


async def _export(
    out: Path, strategy: str | None, first: date | None, last: date | None
) -> list[Path]:
    written: list[Path] = []
    document = ParityDocument()
    async with open_parity_runtime(Settings.default()) as runtime:
        names = [strategy] if strategy else await runtime.store.strategies()
        for name in names:
            sessions, _ = await _sessions(runtime, name, first, last)
            if not sessions:
                continue
            report = runtime.service.assemble(sessions, ParityKind.CUMULATIVE)
            stem = f"{name}_{report.first_session}_{report.last_session}"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{stem}.json").write_text(
                json.dumps(document.to_json(report, sessions), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (out / f"{stem}.md").write_text(document.markdown(report, sessions), encoding="utf-8")
            written += [out / f"{stem}.json", out / f"{stem}.md"]
    return written


@parity_app.command("export")
def parity_export(
    out: Path = _OUT,
    strategy: str | None = typer.Option(None, "--strategy", "-s", help="Only this strategy."),
    first: datetime | None = _FROM,
    last: datetime | None = _TO,
) -> None:
    """Write the rendered Markdown and JSON reports to OUT, for sharing."""
    try:
        written = asyncio.run(
            _export(out, strategy, first.date() if first else None, last.date() if last else None)
        )
    except (EmporosError, ValueError) as error:
        raise _fail("parity export", error) from error
    for path in written:
        typer.echo(str(path))
    if not written:
        typer.echo("no stored parity sessions to export")
