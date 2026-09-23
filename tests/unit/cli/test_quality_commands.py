"""EM-177: the quality/quarantine commands up to the point they would touch Mongo."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

import emporos.cli.quality_commands as quality_commands
from emporos.cli.main import app
from emporos.cli.quality_commands import _quarantine_message
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Timeframe
from emporos.history.quality import Finding, QualityReport, Severity

runner = CliRunner()


@pytest.fixture
def strategy_file(tmp_path: Path) -> Path:
    """`history check`/`quarantine`'s `_STRATEGY` argument only needs to exist: `_audit`/
    `_quarantine` are monkeypatched below, so it is never actually loaded."""
    path = tmp_path / "strategy.yaml"
    path.write_text("name: stub\n", encoding="utf-8")
    return path


def clean_report(errors: int = 0) -> QualityReport:
    findings = (
        (Finding("overnight_discontinuity", "NSE:1", date(2026, 3, 3), Severity.WARNING, "x"),)
        if not errors
        else (Finding("timestamp_alignment", "NSE:1", date(2026, 3, 3), Severity.ERROR, "x"),)
    )
    return QualityReport(Timeframe.M5, date(2026, 3, 2), date(2026, 3, 3), 2, (), findings)


def test_the_history_group_offers_check_and_quarantine() -> None:
    result = runner.invoke(app, ["history", "--help"])

    assert result.exit_code == 0
    assert "check" in result.output and "quarantine" in result.output


def test_quarantine_help_documents_write() -> None:
    result = runner.invoke(app, ["history", "quarantine", "--help"])

    assert result.exit_code == 0 and "--write" in result.output


def test_the_message_reports_hits_and_proposals() -> None:
    assert _quarantine_message(0, 0, write=False) == [
        "0 overnight_discontinuity finding(s), 0 newly proposed"
    ]
    assert _quarantine_message(7, 0, write=False) == [
        "7 overnight_discontinuity finding(s), 0 newly proposed"
    ]


def test_a_preview_with_proposals_says_nothing_was_written() -> None:
    lines = _quarantine_message(7, 3, write=False)

    assert lines[0] == "7 overnight_discontinuity finding(s), 3 newly proposed"
    assert lines[1] == "nothing written: re-run with --write once these are reviewed"


def test_writing_proposals_says_nothing_extra() -> None:
    assert _quarantine_message(7, 3, write=True) == [
        "7 overnight_discontinuity finding(s), 3 newly proposed"
    ]


def test_writing_with_no_new_proposals_says_nothing_extra_either() -> None:
    assert _quarantine_message(7, 0, write=True) == [
        "7 overnight_discontinuity finding(s), 0 newly proposed"
    ]


def test_check_fails_loudly_when_the_audit_raises(
    strategy_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_args: object, **_kwargs: object) -> QualityReport:
        raise ConfigurationError("no database configured")

    monkeypatch.setattr(quality_commands, "_audit", boom)

    result = runner.invoke(
        app, ["history", "check", str(strategy_file), "--from", "2026-03-02", "--to", "2026-03-03"]
    )

    assert result.exit_code == 1 and "check failed: no database configured" in result.output


def test_check_writes_the_report_and_record_on_success(
    strategy_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_audit(*_args: object, **_kwargs: object) -> QualityReport:
        return clean_report()

    monkeypatch.setattr(quality_commands, "_audit", fake_audit)
    report, record = tmp_path / "report.md", tmp_path / "record.json"

    result = runner.invoke(
        app,
        [
            "history", "check", str(strategy_file), "--from", "2026-03-02", "--to", "2026-03-03",
            "--report", str(report), "--json", str(record),
        ],
    )  # fmt: skip

    assert result.exit_code == 0
    assert "0 instruments, 2 market days, 1 findings (0 errors)" in result.output
    assert report.exists() and record.exists()


def test_check_exits_2_when_the_audit_finds_errors(
    strategy_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_audit(*_args: object, **_kwargs: object) -> QualityReport:
        return clean_report(errors=1)

    monkeypatch.setattr(quality_commands, "_audit", fake_audit)

    result = runner.invoke(
        app,
        [
            "history", "check", str(strategy_file), "--from", "2026-03-02", "--to", "2026-03-03",
            "--report", str(tmp_path / "r.md"), "--json", str(tmp_path / "r.json"),
        ],
    )  # fmt: skip

    assert result.exit_code == 2


def test_quarantine_fails_loudly_when_it_raises(
    strategy_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_args: object, **_kwargs: object) -> tuple[QualityReport, int]:
        raise ConfigurationError("no database configured")

    monkeypatch.setattr(quality_commands, "_quarantine", boom)

    result = runner.invoke(
        app,
        ["history", "quarantine", str(strategy_file), "--from", "2026-03-02", "--to", "2026-03-03"],
    )

    assert result.exit_code == 1 and "quarantine failed: no database configured" in result.output


def test_quarantine_previews_proposals_by_default(
    strategy_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_quarantine(*_args: object, **_kwargs: object) -> tuple[QualityReport, int]:
        return clean_report(), 1

    monkeypatch.setattr(quality_commands, "_quarantine", fake_quarantine)

    result = runner.invoke(
        app,
        ["history", "quarantine", str(strategy_file), "--from", "2026-03-02", "--to", "2026-03-03"],
    )

    assert result.exit_code == 0
    assert "1 overnight_discontinuity finding(s), 1 newly proposed" in result.output
    assert "nothing written" in result.output
