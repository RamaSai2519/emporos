"""`emporos halt`, `emporos resume`, `emporos kill-switch status` (plan.md §11, §15.4).

Break-glass commands: each needs nothing but this machine. They set or clear BOTH the file
sentinel and the Mongo flag, report each place separately, and `halt` succeeds if at least one
place took it — with the database unreachable, the sentinel alone still stops the worker.
"""

from __future__ import annotations

import asyncio
import getpass

import typer

from emporos.core.clock import SystemClock
from emporos.core.config import Settings
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.repositories import KillSwitchRepository
from emporos.risk.kill_switch import (
    FileSentinelKillSwitch,
    KillSwitchControl,
    KillSwitchReader,
    KillSwitchWriter,
    MongoKillSwitch,
    SinkOutcome,
)

_REASON = typer.Option("operator halt", "--reason", "-r", help="Why trading is being halted.")
_BY = typer.Option("", "--by", help="Who is doing it (defaults to the OS user).")


class KillSwitchComposer:
    """Wires the control from `Settings`: the file always, Mongo when `MONGO_URL` is set."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._mongo: MongoClientFactory | None = None

    def build(self) -> KillSwitchControl:
        sentinel = FileSentinelKillSwitch(self._settings.kill_switch_path)
        writers: list[KillSwitchWriter] = [sentinel]
        readers: list[KillSwitchReader] = [sentinel]
        if self._settings.mongo_url:
            self._mongo = MongoClientFactory(self._settings)
            flag = MongoKillSwitch(KillSwitchRepository(self._mongo.database()))
            writers.append(flag)
            readers.append(flag)
        return KillSwitchControl(writers, readers, SystemClock())

    async def close(self) -> None:
        if self._mongo is not None:
            await self._mongo.close()


def _show(outcomes: list[SinkOutcome]) -> None:
    for outcome in outcomes:
        state = "" if outcome.halted is None else (" HALTED" if outcome.halted else " clear")
        line = f"  {outcome.name}: {'ok' if outcome.ok else 'FAILED'}{state}"
        if outcome.detail:
            line += f" ({outcome.detail})"
        typer.secho(line, fg=typer.colors.GREEN if outcome.ok else typer.colors.RED)


async def _run(action: str, reason: str, by: str) -> list[SinkOutcome]:
    composer = KillSwitchComposer(Settings.default())
    control = composer.build()
    try:
        if action == "halt":
            return await control.engage(reason, by)
        if action == "resume":
            return await control.release(by)
        return await control.status()
    finally:
        await composer.close()


def halt(reason: str = _REASON, by: str = _BY) -> None:
    """Trip the kill switch: halts all new orders within one poll interval (plan.md §11)."""
    outcomes = asyncio.run(_run("halt", reason, by or getpass.getuser()))
    _show(outcomes)
    if not any(o.ok for o in outcomes):
        typer.secho("NOT HALTED: no place accepted the halt.", fg=typer.colors.RED, bold=True)
        raise typer.Exit(code=1)
    failed = [o.name for o in outcomes if not o.ok]
    if failed:
        typer.secho(f"Halted, but not in: {', '.join(failed)}.", fg=typer.colors.YELLOW)
    else:
        typer.secho("HALTED.", fg=typer.colors.GREEN, bold=True)


def resume(by: str = _BY) -> None:
    """Clear the kill switch everywhere. Trading stays halted if any place cannot be cleared."""
    outcomes = asyncio.run(_run("resume", "", by or getpass.getuser()))
    _show(outcomes)
    if not all(o.ok for o in outcomes):
        typer.secho("Still HALTED in a place that could not be cleared.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho("Kill switch cleared.", fg=typer.colors.GREEN, bold=True)


def kill_switch_status() -> None:
    """Show whether the kill switch is set, place by place."""
    outcomes = asyncio.run(_run("status", "", ""))
    _show(outcomes)
    if any(o.halted for o in outcomes):
        raise typer.Exit(code=2)  # halted: scripts can branch on this
    if not all(o.ok for o in outcomes):
        raise typer.Exit(code=1)  # unreadable somewhere: unknown counts as halted
