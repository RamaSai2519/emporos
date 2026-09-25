"""EM-221: the audit command reads derived daily bars and writes the findings."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml
from typer.testing import CliRunner

from emporos.cli.experiment_commands import research_app
from emporos.persistence.candle_cache import CandleCacheFiles
from emporos.research.d1_universe import D1Manifest, LiquidityRule
from emporos.research.daily_bars import DailyBarStore
from emporos.research.partition import DISCOVERY
from tests.unit.research.test_adjustments import D2, X, daily

RUNNER = CliRunner()


def manifest(path: Path, included: tuple[str, ...]) -> Path:
    D1Manifest(
        "seed", Decimal("0.3"), LiquidityRule(), DISCOVERY, (), ("NSE:99",), included, {}, ()
    ).save(path)
    return path


def test_it_quarantines_an_unexplained_gap_and_writes_the_findings(tmp_path: Path) -> None:
    root = tmp_path / "derived"
    DailyBarStore(CandleCacheFiles(root)).write(
        X, [daily(date(2026, 1, 1), "1000", "1000"), daily(D2, "500", "500")]
    )
    ledger = tmp_path / "adjustments.yaml"
    ledger.write_text("factors: []\n", encoding="utf-8")
    out = tmp_path / "report.yaml"

    result = RUNNER.invoke(
        research_app,
        [
            "audit-discontinuities", "--manifest", str(manifest(tmp_path / "m.yaml", (X,))),
            "--ledger", str(ledger), "--out", str(out), "--root", str(root),
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "1 quarantined" in result.output
    document = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert document["sessions_checked"] == 2
    assert [(f["instrument_id"], f["status"]) for f in document["findings"]] == [
        (X, "split_shaped")
    ]


def test_a_missing_ledger_is_a_clean_failure(tmp_path: Path) -> None:
    result = RUNNER.invoke(
        research_app,
        [
            "audit-discontinuities", "--manifest", str(manifest(tmp_path / "m.yaml", (X,))),
            "--ledger", str(tmp_path / "nope.yaml"), "--out", str(tmp_path / "r.yaml"),
            "--root", str(tmp_path),
        ],
    )  # fmt: skip

    assert result.exit_code == 1
    assert "audit-discontinuities failed" in result.output
