"""EM-225: the collect-fo-archive command parses its defaults and refuses a rushed gap before any
request is made."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from emporos.cli.experiment_commands import research_app


def test_a_gap_under_a_second_is_refused_before_any_request(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        research_app,
        [
            "collect-fo-archive", "--to", "2026-09-18", "--seconds-between-requests", "0.5",
            "--out", str(tmp_path / "fo"), "--ledger", str(tmp_path / "ledger.jsonl"),
        ],
    )  # fmt: skip

    assert result.exit_code == 1
    assert "second apart" in result.output
    assert not (tmp_path / "ledger.jsonl").exists()


def test_build_fo_specs_reports_the_lot_size_check_and_writes_the_file(tmp_path: Path) -> None:
    from datetime import date

    from emporos.research.fo_archive_rows import ArchiveFormat, read_archive
    from emporos.research.fo_archive_store import FoDayStore
    from emporos.research.fo_contract_specs import read_specs
    from tests.unit.research.test_fo_archive import DAY, UDIFF_CSV, zipped

    FoDayStore(tmp_path).write(
        DAY, read_archive(DAY, zipped("x.csv", UDIFF_CSV), ArchiveFormat.UDIFF)
    )

    result = CliRunner().invoke(research_app, ["build-fo-specs", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "1 spec(s) over 1 day(s)" in result.output
    (spec,) = read_specs(tmp_path / "contract_specs.parquet")
    assert (spec.symbol, spec.lot_size, spec.day) == ("NIFTY", 25, date(2024, 9, 30))
    assert "agree" in result.output


def test_build_fo_specs_needs_stored_days(tmp_path: Path) -> None:
    result = CliRunner().invoke(research_app, ["build-fo-specs", "--out", str(tmp_path)])

    assert result.exit_code == 1 and "run collect-fo-archive first" in result.output
