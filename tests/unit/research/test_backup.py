"""EM-244: the dataset backup builds upload-only sync commands and reports each result."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from emporos.research.backup import DatasetBackup

BUCKET = "s3://b/research-backup"


class Runner:
    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.commands: list[list[str]] = []
        self._codes = codes or {}

    def run(self, command: Sequence[str]) -> int:
        self.commands.append(list(command))
        return self._codes.get(command[3].rsplit("/", 1)[-1], 0)


def root(tmp_path: Path) -> Path:
    for name in ("filings", "events", "fo_stock_v1"):
        (tmp_path / name).mkdir()
    (tmp_path / "loose-file.txt").write_text("not a dataset")
    return tmp_path


def test_every_dataset_directory_is_synced_upload_only(tmp_path: Path) -> None:
    runner = Runner()

    results = DatasetBackup(root(tmp_path), BUCKET, runner).run()

    assert [r.dataset for r in results] == ["events", "filings", "fo_stock_v1"]
    first = runner.commands[0]
    assert first[:3] == ["aws", "s3", "sync"] and first[4] == f"{BUCKET}/events/"
    assert all(
        "--delete" not in c for c in runner.commands
    )  # a wiped source cannot empty the backup
    assert "--exclude" in first and "*.tmp" in first


def test_named_datasets_only_and_dry_run(tmp_path: Path) -> None:
    runner = Runner()

    DatasetBackup(root(tmp_path), BUCKET, runner).run(["filings"], dry_run=True)

    assert len(runner.commands) == 1 and runner.commands[0][-1] == "--dryrun"


def test_an_unknown_dataset_and_a_bad_destination_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nope"):
        DatasetBackup(root(tmp_path), BUCKET, Runner()).run(["nope"])
    with pytest.raises(ValueError, match="s3://"):
        DatasetBackup(tmp_path, "/local/path", Runner())


def test_a_failed_sync_is_reported_not_hidden(tmp_path: Path) -> None:
    results = DatasetBackup(root(tmp_path), BUCKET, Runner({"filings": 1})).run()

    assert {r.dataset: r.ok for r in results} == {
        "events": True,
        "filings": False,
        "fo_stock_v1": True,
    }


def test_a_missing_research_dir_has_no_datasets(tmp_path: Path) -> None:
    assert DatasetBackup(tmp_path / "absent", BUCKET, Runner()).run() == []
