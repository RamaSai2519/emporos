"""`emporos graduation ...`: the operator's road from RESEARCH to LIVE_CONSERVATIVE (EM-189).

CLI only, on purpose. The human acknowledgement is a typed phrase at a terminal: it is not exposed
through the API or the dashboard (the API has a read-only view of the result and no write route),
consistent with the kill switch not depending on the dashboard. `acknowledge` refuses to run
without an interactive terminal, so a script or a pipe cannot supply the phrase for the operator.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from collections.abc import Awaitable, Callable

import typer

from emporos.cli.graduation_console import GraduationConsole, Outcome
from emporos.cli.graduation_runtime import GraduationComposer
from emporos.core.clock import SystemClock
from emporos.core.config import Settings
from emporos.core.errors import EmporosError
from emporos.domain.graduation import GraduationStage

graduation_app = typer.Typer(
    help="Promote a strategy configuration towards live, on evidence. CLI only.",
    no_args_is_help=True,
)

_STRATEGY = typer.Argument(..., help="Strategy name, as in config/strategies/<name>.yaml.")
_STRATEGIES = typer.Argument(None, help="Strategies to show (default: all).")
_BY = typer.Option("", "--by", help="Who is doing it (defaults to the OS user).")
_EXPERIMENT = typer.Option(None, "--experiment", help="The published EM-188 experiment id cited.")
_JEV = typer.Option(
    False, "--jev-enabled", help="This deployment runs the strategy with Jev in the loop."
)


def _stage(value: str) -> GraduationStage:
    try:
        return GraduationStage(value)
    except ValueError:
        raise typer.BadParameter(
            f"{value!r} is not a stage; one of {', '.join(s.value for s in GraduationStage)}"
        ) from None


async def _with_console(action: Callable[[GraduationConsole], Awaitable[Outcome]]) -> Outcome:
    async with GraduationComposer(Settings.default(), SystemClock()).open() as console:
        return await action(console)


def _run(action: Callable[[GraduationConsole], Awaitable[Outcome]]) -> None:
    try:
        outcome = asyncio.run(_with_console(action))
    except (EmporosError, ValueError, OSError) as error:
        message = error.message if isinstance(error, EmporosError) else str(error)
        typer.secho(f"graduation failed: {message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    for line in outcome.lines:
        typer.echo(line)
    if not outcome.ok:
        raise typer.Exit(code=1)


@graduation_app.command("status")
def graduation_status(
    strategy: list[str] | None = _STRATEGIES,
    jev_enabled: bool = _JEV,
) -> None:
    """Each strategy's stage, and which requirements of the next stage are met."""
    _run(lambda c: c.status(strategy or [], jev_enabled))


@graduation_app.command("promote")
def graduation_promote(
    strategy: str = _STRATEGY,
    to: str = typer.Option(..., "--to", help="paper or live_conservative."),
    experiment: str | None = _EXPERIMENT,
    jev_enabled: bool = _JEV,
    by: str = _BY,
) -> None:
    """Promote one stage up. Prints every unmet requirement, or records the promotion."""
    target = _stage(to)
    _run(lambda c: c.promote(strategy, target, by or getpass.getuser(), experiment, jev_enabled))


@graduation_app.command("demote")
def graduation_demote(
    strategy: str = _STRATEGY,
    to: str = typer.Option(..., "--to", help="The stage to fall back to."),
    reason: str = typer.Option(..., "--reason", help="Why (recorded)."),
    by: str = _BY,
) -> None:
    """Demote to a lower stage. Always allowed, always recorded."""
    target = _stage(to)
    _run(lambda c: c.demote(strategy, target, by or getpass.getuser(), reason))


@graduation_app.command("history")
def graduation_history(strategy: str = _STRATEGY) -> None:
    """Every graduation event for a strategy, oldest first."""
    _run(lambda c: c.history(strategy))


@graduation_app.command("acknowledge")
def graduation_acknowledge(
    strategy: str = _STRATEGY,
    experiment: str | None = _EXPERIMENT,
    by: str = _BY,
) -> None:
    """Show what going live means and record your acknowledgement. Needs a terminal.

    You will be shown the risk tier, the capital, the limits and the evidence, then asked to type
    `<strategy>@<hash8> LIVE` exactly. Nothing else records an acknowledgement.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        typer.secho(
            "acknowledge needs an interactive terminal: a person types the phrase.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    who = by or getpass.getuser()
    _run(lambda c: c.acknowledge(strategy, who, typer.prompt, experiment))
