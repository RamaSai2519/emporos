"""`emporos halt` / `resume` / `kill-switch status` (EM-74): standalone, file-first, honest about
which place took the halt. These never touch the shared dev database's real kill switch: the
settings point at a temporary sentinel and either no Mongo at all or one that cannot be reached."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from emporos.cli.main import app
from emporos.core.config import Settings

runner = CliRunner()
UNREACHABLE_MONGO = "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=200"


@pytest.fixture
def sentinel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Settings with only a temporary sentinel: no database, no .env, no other subsystem."""
    path = tmp_path / "state" / "HALT"
    settings = Settings(_env_file=None, KILL_SWITCH_FILE=str(path), MONGO_URL=None)  # type: ignore[call-arg]
    monkeypatch.setattr(Settings, "default", classmethod(lambda cls: settings))
    return path


def test_halt_works_standalone_with_only_a_file_and_no_database(sentinel: Path) -> None:
    result = runner.invoke(app, ["halt", "--reason", "drill", "--by", "rama"])

    assert result.exit_code == 0 and "HALTED" in result.output
    assert sentinel.read_text().startswith("drill")
    assert "mongo" not in result.output  # nothing else was involved


def test_halt_with_no_reason_still_halts_because_a_panic_needs_no_arguments(sentinel: Path) -> None:
    result = runner.invoke(app, ["halt"])
    assert result.exit_code == 0 and sentinel.read_text().startswith("operator halt")


def test_halt_lands_the_sentinel_even_when_mongo_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "HALT"
    settings = Settings(_env_file=None, KILL_SWITCH_FILE=str(path), MONGO_URL=UNREACHABLE_MONGO)  # type: ignore[call-arg]
    monkeypatch.setattr(Settings, "default", classmethod(lambda cls: settings))

    result = runner.invoke(app, ["halt", "--reason", "db outage"])

    assert result.exit_code == 0
    assert path.exists()
    assert "file: ok" in result.output and "mongo: FAILED" in result.output
    assert "Halted, but not in: mongo" in result.output


def test_halt_fails_loudly_when_no_place_accepts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "afile"
    blocker.write_text(
        "x"
    )  # a file where the sentinel's directory should be: nothing can be written
    settings = Settings(_env_file=None, KILL_SWITCH_FILE=str(blocker / "HALT"), MONGO_URL=None)  # type: ignore[call-arg]
    monkeypatch.setattr(Settings, "default", classmethod(lambda cls: settings))

    result = runner.invoke(app, ["halt"])

    assert result.exit_code == 1 and "NOT HALTED" in result.output


def test_status_reports_clear_then_halted_with_a_distinct_exit_code(sentinel: Path) -> None:
    clear = runner.invoke(app, ["kill-switch", "status"])
    assert clear.exit_code == 0 and "clear" in clear.output

    runner.invoke(app, ["halt", "--reason", "drill"])
    halted = runner.invoke(app, ["kill-switch", "status"])
    assert halted.exit_code == 2 and "HALTED" in halted.output


def test_resume_clears_the_sentinel(sentinel: Path) -> None:
    runner.invoke(app, ["halt"])
    result = runner.invoke(app, ["resume", "--by", "rama"])

    assert result.exit_code == 0 and "cleared" in result.output
    assert not sentinel.exists()


def test_resume_reports_failure_when_a_place_cannot_be_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None, KILL_SWITCH_FILE=str(tmp_path / "HALT"), MONGO_URL=UNREACHABLE_MONGO
    )  # type: ignore[call-arg]
    monkeypatch.setattr(Settings, "default", classmethod(lambda cls: settings))
    (tmp_path / "HALT").write_text("x")

    result = runner.invoke(app, ["resume"])

    assert result.exit_code == 1 and "Still HALTED" in result.output


def test_status_exits_nonzero_when_a_place_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None, KILL_SWITCH_FILE=str(tmp_path / "HALT"), MONGO_URL=UNREACHABLE_MONGO
    )  # type: ignore[call-arg]
    monkeypatch.setattr(Settings, "default", classmethod(lambda cls: settings))

    result = runner.invoke(app, ["kill-switch", "status"])

    assert result.exit_code == 1 and "mongo: FAILED" in result.output
