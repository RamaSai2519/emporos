"""`emporos research dev-autorun l1` — Track L's Dev with nobody watching (EM-240).

One firing of a systemd timer: freeze the events when the 2024 material tier is read, smoke-test
the model, then run v1..v5 in order, each as its own `run-llm-variant` process. A firing does the
next thing that can be done and stops; the state file and the journal make the next firing resume.
gpt-4o is never called from here: the arbiter variant is not in the list."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import typer

from emporos.cli.llm_commands import DEFAULT_JOURNAL, DEFAULT_REPORTS, EXIT_INCOMPLETE
from emporos.core.clock import SystemClock
from emporos.core.paths import research_dir
from emporos.eventtrader.autorun import Autorun, Outcome, Step, StepResult
from emporos.research.filings.snapshot import DEFAULT_SNAPSHOT_DIR, RECORD

DEFAULT_DIRECTORY = research_dir() / "track-l"
SNAPSHOT_NAME = "dev-2024-v1"
DEV_YEAR = 2024
VARIANTS = ("v1_t60", "v2_t75", "v3_nopanel_t60", "v4_posture_t60", "v5_posture_t75")
TAIL_LINES = 6
EXIT_PENDING = 3  # `research extraction-progress`: the tier is not fully read yet
SLICE = 540  # attachments per firing at the extractor's 3 s gap: about 27 minutes


class CommandRunner(Protocol):
    def run(self, name: str, arguments: Sequence[str]) -> tuple[int, str]: ...


class Runner:
    """Runs one `emporos research ...` command in its own process, its output in a log file."""

    def __init__(self, logs: Path, cwd: Path) -> None:
        self._logs, self._cwd = logs, cwd

    def run(self, name: str, arguments: Sequence[str]) -> tuple[int, str]:
        """(exit code, the last lines of its output)."""
        self._logs.mkdir(parents=True, exist_ok=True)
        log = self._logs / f"{name}.log"
        command = [sys.executable, "-m", "emporos.cli", "research", *arguments]
        with log.open("a", encoding="utf-8") as handle:
            done = subprocess.run(
                command, cwd=self._cwd, stdout=handle, stderr=subprocess.STDOUT, check=False
            )
        tail = log.read_text(encoding="utf-8").splitlines()[-TAIL_LINES:]
        return done.returncode, " / ".join(line.strip() for line in tail if line.strip())


class ExtractorProbe(Protocol):
    def running(self) -> bool:
        """True while a `research extract-filing-text` process is alive (this one or another)."""
        ...


class ProcessProbe:
    def running(self) -> bool:
        found = subprocess.run(
            ["pgrep", "-f", "emporos.cli research extract-filing-text"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        return bool(found.stdout.split())


class ExtractionStep:
    """Waits for the 2024 material tier's attachment text. When no extractor is alive it does a
    slice of the work itself, in the foreground of this firing (the extractor resumes from its
    store), so nothing has to be left running between firings."""

    name = "extraction"

    def __init__(self, runner: CommandRunner, probe: ExtractorProbe) -> None:
        self._runner, self._probe = runner, probe

    def run(self) -> StepResult:
        code, detail = self._progress()
        if code == 0:
            return StepResult(Outcome.DONE, detail)
        if code != EXIT_PENDING:
            return StepResult(Outcome.FAILED, f"extraction-progress exit {code}: {detail}")
        if self._probe.running():
            return StepResult(Outcome.WAIT, f"an extractor is running; {detail}")
        worked, tail = self._runner.run(
            "extract-filing-text", ["extract-filing-text", "--limit", str(SLICE)]
        )
        code, detail = self._progress()
        if code == 0:
            return StepResult(Outcome.DONE, detail)
        return StepResult(Outcome.WAIT, f"extracted a slice (exit {worked}: {tail}); {detail}")

    def _progress(self) -> tuple[int, str]:
        return self._runner.run(
            self.name,
            ["extraction-progress", "--first", f"{DEV_YEAR}-01-01", "--to", f"{DEV_YEAR}-12-31"],
        )


class FreezeStep:
    name = "freeze-events"

    def __init__(
        self, runner: CommandRunner, snapshots: Path, snapshot: str = SNAPSHOT_NAME
    ) -> None:
        self._runner, self._snapshots, self._snapshot = runner, snapshots, snapshot

    def run(self) -> StepResult:
        if (self._snapshots / self._snapshot / RECORD).exists():
            return StepResult(Outcome.DONE, f"{self._snapshot} already frozen")
        code, tail = self._runner.run(
            self.name, ["freeze-events", self._snapshot, "--snapshots", str(self._snapshots)]
        )
        if code:
            return StepResult(Outcome.FAILED, f"freeze-events exit {code}: {tail}")
        return StepResult(Outcome.DONE, tail)


class SmokeStep:
    name = "smoke"

    def __init__(self, runner: CommandRunner, events: Path, journal: Path) -> None:
        self._runner, self._events, self._journal = runner, events, journal

    def run(self) -> StepResult:
        code, tail = self._runner.run(
            self.name,
            ["llm-smoke", "--events-dir", str(self._events), "--journal", str(self._journal)],
        )
        if code:
            return StepResult(Outcome.FAILED, f"smoke exit {code}: {tail}")
        return StepResult(Outcome.DONE, tail)


class VariantStep:
    def __init__(
        self,
        variant: str,
        runner: CommandRunner,
        events: Path,
        snapshot: str,
        journal: Path,
        reports: Path,
    ) -> None:
        self.name = variant
        self._runner, self._events, self._snapshot = runner, events, snapshot
        self._journal, self._reports = journal, reports

    def run(self) -> StepResult:
        code, tail = self._runner.run(
            self.name,
            [
                "run-llm-variant", self.name, "--events-dir", str(self._events),
                "--snapshot", self._snapshot, "--journal", str(self._journal),
                "--reports", str(self._reports),
            ],
        )  # fmt: skip
        if code == EXIT_INCOMPLETE:
            return StepResult(Outcome.INCOMPLETE, tail)
        if code:
            return StepResult(Outcome.FAILED, f"run-llm-variant exit {code}: {tail}")
        return StepResult(Outcome.DONE, self._headline())

    def _headline(self) -> str:
        path = self._reports / f"{self.name}-summary.json"
        s: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return (
            f"trades {s['trades']}, net benchmark {s['net_benchmark']} adverse "
            f"{s['net_adverse']}, coin p {s['control_p']}, fresh {s['fresh_calls']} "
            f"hits {s['journal_hits']}"
        )


class TableStep:
    name = "table"

    def __init__(self, runner: CommandRunner, reports: Path) -> None:
        self._runner, self._reports = runner, reports

    def run(self) -> StepResult:
        code, tail = self._runner.run(
            self.name, ["llm-variant-table", "--reports", str(self._reports)]
        )
        if code:
            return StepResult(Outcome.FAILED, f"llm-variant-table exit {code}: {tail}")
        return StepResult(Outcome.DONE, "table printed to the log")


def build_steps(
    directory: Path,
    snapshots: Path,
    journal: Path,
    reports: Path,
    cwd: Path,
    probe: ExtractorProbe | None = None,
) -> list[Step]:
    runner = Runner(directory / "logs", cwd)
    events = snapshots / SNAPSHOT_NAME
    steps: list[Step] = [
        ExtractionStep(runner, probe or ProcessProbe()),
        FreezeStep(runner, snapshots),
        SmokeStep(runner, events, journal),
    ]
    steps += [VariantStep(v, runner, events, SNAPSHOT_NAME, journal, reports) for v in VARIANTS]
    steps.append(TableStep(runner, reports))
    return steps


_DIRECTORY = typer.Option(DEFAULT_DIRECTORY, help="State, lock, logs and STATUS.md.")
_SNAPSHOTS = typer.Option(DEFAULT_SNAPSHOT_DIR, help="Where frozen snapshots live.")
_JOURNAL = typer.Option(DEFAULT_JOURNAL, help="The call journal.")
_REPORTS = typer.Option(DEFAULT_REPORTS, help="Where the reports go.")


def research_dev_autorun(
    track: str = typer.Argument(..., help="The run to fire: l1."),
    directory: Path = _DIRECTORY,
    snapshots: Path = _SNAPSHOTS,
    journal: Path = _JOURNAL,
    reports: Path = _REPORTS,
) -> None:
    """One firing of the unattended Track L Dev run; exit 1 when it is halted."""
    if track != "l1":
        typer.secho(f"dev-autorun: unknown run {track!r} (only l1)", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    steps = build_steps(directory, snapshots, journal, reports, Path.cwd())
    autorun = Autorun(steps, directory, SystemClock(), typer.echo)
    code = autorun.run_once()
    if code:
        raise typer.Exit(code=code)
