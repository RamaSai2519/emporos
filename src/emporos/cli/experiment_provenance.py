"""What a published experiment report says about the code and the cost model behind it (EM-188).

`GitRepository` is the only place the CLI shells out to git: the revision a result was produced
by, and whether a declaration was committed before it was used (declared-before-run, by version
control rather than by trust)."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime
from pathlib import Path

from emporos.backtest.robustness.benchmark import BenchmarkConfig
from emporos.domain.fees import FeeSchedule
from emporos.domain.research_experiments import CostModelVersion, VersionStamp


class GitRepository:
    def __init__(self, root: Path = Path()) -> None:
        self._root = root

    def revision(self) -> str | None:
        """The short HEAD revision, marked `+dirty` when tracked files differ from it; None when
        this is not a git checkout."""
        head = self._git("rev-parse", "--short", "HEAD")
        if head is None:
            return None
        changed = self._git("status", "--porcelain", "--untracked-files=no")
        return f"{head}+dirty" if changed else head

    def is_committed(self, path: Path) -> bool:
        """True only for a tracked file with no uncommitted change. Unknown counts as False."""
        tracked = self._git("ls-files", "--error-unmatch", "--", str(path))
        pending = self._git("status", "--porcelain", "--", str(path))
        return tracked is not None and pending == ""

    def first_commit_time(self, path: Path) -> datetime | None:
        """When the file was first committed (its oldest commit), or None if it never was."""
        found = self._git("log", "--diff-filter=A", "--format=%cI", "--", str(path))
        if not found:
            return None
        return datetime.fromisoformat(found.splitlines()[-1])

    def _git(self, *args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args],
                cwd=self._root,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None


class CurationVersionStamp:
    """The versions that are the same for every strategy of one curation run."""

    def __init__(self, git: GitRepository) -> None:
        self._git = git

    def of(
        self, benchmark_file: Path, benchmark: BenchmarkConfig, schedule: FeeSchedule
    ) -> VersionStamp:
        digest = hashlib.sha256(benchmark_file.read_bytes()).hexdigest()
        return VersionStamp(
            cost_model=CostModelVersion(schedule.name, benchmark.slippage_bps, f"sha256:{digest}"),
            code_revision=self._git.revision(),
        )
