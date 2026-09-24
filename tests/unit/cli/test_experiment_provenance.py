"""EM-188: the revision and the committed-declaration check, against a real throwaway repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.cli.experiment_provenance import CurationVersionStamp, GitRepository
from emporos.portfolio.fee_schedules import FeeScheduleLibrary


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "declared.yaml").write_text("a: 1\n")
    git(tmp_path, "add", "declared.yaml")
    git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


class TestIsCommitted:
    def test_a_committed_unchanged_file_is(self, repo: Path) -> None:
        assert GitRepository(repo).is_committed(Path("declared.yaml"))

    def test_an_edited_file_is_not(self, repo: Path) -> None:
        (repo / "declared.yaml").write_text("a: 2\n")

        assert not GitRepository(repo).is_committed(Path("declared.yaml"))

    def test_an_untracked_file_is_not(self, repo: Path) -> None:
        (repo / "new.yaml").write_text("b: 1\n")

        assert not GitRepository(repo).is_committed(Path("new.yaml"))

    def test_outside_a_repository_nothing_is_provably_committed(self, tmp_path: Path) -> None:
        (tmp_path / "x.yaml").write_text("a: 1\n")

        assert not GitRepository(tmp_path).is_committed(Path("x.yaml"))


class TestRevision:
    def test_a_clean_checkout_is_its_short_head(self, repo: Path) -> None:
        revision = GitRepository(repo).revision()

        assert revision is not None and "+dirty" not in revision and len(revision) >= 7

    def test_a_modified_tracked_file_marks_it_dirty(self, repo: Path) -> None:
        (repo / "declared.yaml").write_text("a: 2\n")

        revision = GitRepository(repo).revision()

        assert revision is not None and revision.endswith("+dirty")

    def test_untracked_files_do_not_mark_it_dirty(self, repo: Path) -> None:
        (repo / "scratch.txt").write_text("x")

        revision = GitRepository(repo).revision()

        assert revision is not None and not revision.endswith("+dirty")

    def test_outside_a_repository_it_is_unknown(self, tmp_path: Path) -> None:
        assert GitRepository(tmp_path).revision() is None


class TestCurationVersionStamp:
    def test_it_records_the_fee_schedule_slippage_benchmark_hash_and_revision(
        self, repo: Path
    ) -> None:
        benchmark = BenchmarkLoader().load()
        library = FeeScheduleLibrary.from_directory()
        benchmark_file = repo / "benchmark.yaml"
        benchmark_file.write_text("capital: 50000\n")

        stamp = CurationVersionStamp(GitRepository(repo)).of(
            benchmark_file, benchmark, library.earliest
        )

        assert stamp.cost_model is not None
        assert stamp.cost_model.fee_schedule_id == library.earliest.name
        assert stamp.cost_model.slippage_bps == benchmark.slippage_bps
        assert stamp.cost_model.benchmark_hash is not None
        assert stamp.cost_model.benchmark_hash.startswith("sha256:")
        assert stamp.code_revision is not None

    def test_the_benchmark_hash_follows_the_file_content(self, repo: Path) -> None:
        benchmark, schedule = BenchmarkLoader().load(), FeeScheduleLibrary.from_directory().earliest
        a, b = repo / "a.yaml", repo / "b.yaml"
        a.write_text("x: 1\n")
        b.write_text("x: 2\n")
        stamper = CurationVersionStamp(GitRepository(repo))

        hash_a = stamper.of(a, benchmark, schedule).cost_model
        hash_b = stamper.of(b, benchmark, schedule).cost_model

        assert hash_a is not None and hash_b is not None
        assert hash_a.benchmark_hash != hash_b.benchmark_hash
