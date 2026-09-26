"""Backing up research datasets to our own S3 bucket (EM-244, after the 2026-09-26 cache wipe).

`DatasetBackup` copies each dataset directory under the research dir to
`<bucket>/research-backup/<dataset>/` with `aws s3 sync` (an upload only: nothing is deleted at the
destination, so a wiped source cannot empty the backup). The command runner is injected, so the
plan and the commands are tested without touching AWS."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = ["DEFAULT_BUCKET", "CommandRunner", "DatasetBackup", "SubprocessRunner", "SyncResult"]

DEFAULT_BUCKET = "s3://emporos-cold-135808951082-ap-south-1/research-backup"
EXCLUDES = ("*.tmp", "*.tmp.npz", "*.lock")


class CommandRunner(Protocol):
    def run(self, command: Sequence[str]) -> int:
        """Run a command; its exit code."""
        ...


class SubprocessRunner:
    def run(self, command: Sequence[str]) -> int:
        return subprocess.run(list(command), check=False).returncode


@dataclass(frozen=True)
class SyncResult:
    dataset: str
    source: Path
    destination: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class DatasetBackup:
    def __init__(self, root: Path, bucket: str, runner: CommandRunner) -> None:
        if not bucket.startswith("s3://"):
            raise ValueError("the backup destination is an s3:// URI")
        self._root, self._bucket, self._runner = root, bucket.rstrip("/"), runner

    def datasets(self) -> list[str]:
        """Every dataset directory that exists under the research dir."""
        if not self._root.exists():
            return []
        return sorted(p.name for p in self._root.iterdir() if p.is_dir())

    def command(self, dataset: str, dry_run: bool = False) -> list[str]:
        command = [
            "aws", "s3", "sync", str(self._root / dataset), f"{self._bucket}/{dataset}/",
            "--only-show-errors",
        ]  # fmt: skip
        for pattern in EXCLUDES:
            command += ["--exclude", pattern]
        return command + (["--dryrun"] if dry_run else [])

    def run(self, only: Sequence[str] = (), dry_run: bool = False) -> list[SyncResult]:
        present = self.datasets()
        unknown = sorted(set(only) - set(present))
        if unknown:
            raise ValueError(f"not under {self._root}: {', '.join(unknown)}")
        results: list[SyncResult] = []
        for dataset in only or present:
            code = self._runner.run(self.command(dataset, dry_run))
            results.append(
                SyncResult(dataset, self._root / dataset, f"{self._bucket}/{dataset}/", code)
            )
        return results
