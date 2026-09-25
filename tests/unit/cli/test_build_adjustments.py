"""EM-221: build-adjustments explains gaps in the derived bars by the recorded actions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import yaml
from typer.testing import CliRunner

from emporos.cli.experiment_commands import research_app
from emporos.persistence.candle_cache import CandleCacheFiles
from emporos.research.corporate_actions import CorporateAction, CorporateActionLedger
from emporos.research.d1_universe import D1Manifest, LiquidityRule
from emporos.research.daily_bars import DailyBarStore
from emporos.research.partition import DISCOVERY
from tests.unit.research.swing.support import bar, sessions

RUNNER = CliRunner()
DAYS = sessions(60, date(2019, 1, 1))


def test_a_gap_that_fits_a_later_bonus_becomes_a_factor_dated_at_the_gap(tmp_path: Path) -> None:
    root = tmp_path / "derived"
    # raw basis for 30 sessions, then the broker's adjusted basis (half): a gap of 0.5 on DAYS[30]
    bars = [bar("NSE:1", d, "200", "200") for d in DAYS[:30]] + [
        bar("NSE:1", d, "100", "100") for d in DAYS[30:]
    ]
    DailyBarStore(CandleCacheFiles(root)).write("NSE:1", bars)
    D1Manifest(
        "seed", Decimal("0.3"), LiquidityRule(), DISCOVERY, (), ("NSE:99",), ("NSE:1",), {}, ()
    ).save(tmp_path / "manifest.yaml")  # fmt: skip
    (tmp_path / "tokens.csv").write_text("Symbol,Token\nAAA,1\n", encoding="utf-8")
    CorporateActionLedger(tmp_path / "a.jsonl", tmp_path / "c.jsonl").record(
        "AAA", [CorporateAction("AAA", date(2019, 6, 3), "Bonus 1:1")],
        date(2016, 10, 3), date(2026, 3, 18), datetime(2026, 9, 25, tzinfo=UTC), "https://x",
    )  # fmt: skip
    (tmp_path / "corporate-actions.jsonl").write_text(
        (tmp_path / "a.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )

    result = RUNNER.invoke(
        research_app,
        [
            "build-adjustments", "--manifest", str(tmp_path / "manifest.yaml"),
            "--tokens", str(tmp_path / "tokens.csv"), "--actions", str(tmp_path),
            "--out", str(tmp_path / "adjustments.yaml"), "--review", str(tmp_path / "review.yaml"),
            "--root", str(root),
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "1 gaps >= 15%" in result.output
    assert "1 explained" in result.output
    (factor,) = yaml.safe_load((tmp_path / "adjustments.yaml").read_text(encoding="utf-8"))[
        "factors"
    ]
    assert factor["ex_date"] == DAYS[30].isoformat()
    assert factor["ratio"] == "0.5"
    assert "changes basis" in factor["source"]
