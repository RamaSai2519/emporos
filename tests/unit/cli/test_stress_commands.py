"""EM-235: the stress-book command on a tiny synthetic world and a sealed candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from emporos.cli.experiment_commands import research_app
from tests.unit.cli.test_book_commands import world

RUNNER = CliRunner()
SCREEN = "SWG-frozen"


def candidate(tmp_path: Path) -> Path:
    pinned = tmp_path / "declaration.yaml"
    pinned.write_text("declared", encoding="utf-8")
    digest = hashlib.sha256(b"declared").hexdigest()
    path = tmp_path / "candidate.yaml"
    path.write_text(
        "candidate: c60\ncell: a4-momentum-rotation-book\narm: {split: '60/40'}\n"
        f"screen_id: {SCREEN}\ncode_commit: abcdef1234\n"
        f"declaration: {{file: {pinned}, sha256: {digest}}}\n",
        encoding="utf-8",
    )
    return path


def args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str) -> list[str]:
    base = world(tmp_path, monkeypatch)
    discovery = tmp_path / "discovery.jsonl"
    discovery.write_text(json.dumps({"screen_id": SCREEN, "neighbour_share": 1.0}) + "\n", "utf-8")
    keep = {"--manifest", "--adjustments", "--etf-report", "--ledger", "--pnl-dir", "--root"}
    options = [
        x for i, x in enumerate(base) if x in keep or (i and base[i - 1] in keep)
    ]  # the world's options, without its own slug, first-day and bootstrap settings
    return [
        "stress-book", variant, "--candidate-file", str(candidate(tmp_path)),
        "--discovery-ledger", str(discovery), "--bootstrap-paths", "20", *options,
    ]  # fmt: skip


def test_late_listed_out_runs_the_frozen_arm_once_and_records_a_robustness_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = RUNNER.invoke(research_app, args(tmp_path, monkeypatch, "late-listed-out"))

    assert result.exit_code == 0, result.output
    (line,) = (tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(line)
    assert row["kind"] == "robustness"
    assert row["parameters"] == {"split": "60/40", "stress": "late-listed-out"}
    assert row["neighbour_share"] == 1.0  # the Discovery cell's, not a fresh one
    assert "STRESS late-listed-out of frozen candidate c60" in result.output
    assert "equity universe: 2 names before" in result.output


def test_it_refuses_when_a_pinned_input_changed_after_the_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = args(tmp_path, monkeypatch, "late-listed-out")
    (tmp_path / "declaration.yaml").write_text("edited", encoding="utf-8")

    result = RUNNER.invoke(research_app, options)

    assert result.exit_code == 1
    assert "frozen and these inputs changed" in result.output
    assert not (tmp_path / "screens.jsonl").exists()


def test_an_unknown_stress_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = RUNNER.invoke(research_app, args(tmp_path, monkeypatch, "wishful-thinking"))

    assert result.exit_code == 1
    assert "no stress" in result.output


def membership_files(tmp_path: Path, changes: str) -> list[str]:
    d1 = tmp_path / "d1"
    d1.mkdir()
    header = "Company Name,Industry,Symbol,Series,ISIN Code\n"
    (d1 / "nifty100.csv").write_text(header + "A Ltd.,X,AAA,EQ,I1\nB Ltd.,X,BBB,EQ,I2\n", "utf-8")
    (d1 / "niftymidcap150.csv").write_text(header + "C Ltd.,X,CCC,EQ,I3\n", encoding="utf-8")
    (tmp_path / "tokens.csv").write_text("Symbol,Token\nAAA,1\nBBB,2\n", encoding="utf-8")
    (tmp_path / "changes.yaml").write_text(changes, encoding="utf-8")
    (tmp_path / "aliases.yaml").write_text("aliases: []\n", encoding="utf-8")
    return [
        "--d1", str(d1), "--tokens", str(tmp_path / "tokens.csv"),
        "--changes", str(tmp_path / "changes.yaml"), "--aliases", str(tmp_path / "aliases.yaml"),
    ]  # fmt: skip


def test_as_of_membership_keeps_every_name_and_reports_the_coverage_it_stands_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = args(tmp_path, monkeypatch, "as-of-membership")
    options += membership_files(tmp_path, "changes: []\n")

    result = RUNNER.invoke(research_app, options)

    assert result.exit_code == 0, result.output
    assert "0 removed, as-of membership applied" in result.output
    assert "membership coverage: changes on record from None" in result.output
    assert "D1 names that were a member on no day of the window" in result.output


def test_late_joiners_out_drops_a_name_that_entered_the_indices_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changes = (
        "changes:\n- {index: NIFTY 100, effective: '2018-01-15', added: [AAA], removed: [],"
        " source: test}\n"
    )
    options = args(tmp_path, monkeypatch, "late-joiners-out")
    options += membership_files(tmp_path, changes)

    result = RUNNER.invoke(research_app, options)

    assert result.exit_code == 0, result.output
    assert "out: NSE:1 (entered NIFTY 100 / Midcap 150 on 2018-01-15)" in result.output
