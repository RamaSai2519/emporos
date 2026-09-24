"""EM-188: discovering the published reports, dating them, and backfilling the registry."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.backtest.experiment_backfill import ReportBackfill
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.backtest.robustness.verdict import VerdictPolicy
from emporos.cli.experiment_backfill import BackfillSources, ExperimentBackfill
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.experiment_registry import INDEX_JSON, FileExperimentRegistry
from emporos.core.errors import ConfigurationError
from emporos.domain.experiments import TrialRole
from emporos.domain.feature_trials import FeatureTrial
from emporos.domain.hypotheses import HypothesisDeclaration
from emporos.domain.research_experiments import ExperimentFamily
from emporos.research.experiment_report import FeatureLedgerEvidence

GATE_CODES = {
    g.name: g.code for g in VerdictPolicy.standard(BenchmarkLoader().load().verdict).gates
}


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def entry(strategy: str) -> dict[str, object]:
    return {
        "strategy": strategy,
        "passed": False,
        "candidates": ["a", "b"],
        "out_of_sample": {
            "trades": 10,
            "net_pnl": "-5.00",
            "gross_pnl": "-1.00",
            "charges": "4.00",
            "win_rate": "0.3",
            "profit_factor": "0.5",
            "window_nets": ["-5.00"],
            "window_max_drawdowns": ["0.01"],
        },
        "windows": [["2026-01-30", "2026-03-13", "a"]],
        "checks": [
            {"name": "net profit is positive", "passed": False, "actual": "-5", "required": "> 0"}
        ],
        "robustness": None,
    }


@pytest.fixture
def strategies(tmp_path: Path) -> Path:
    root = tmp_path / "docs" / "strategies"
    for sub in ("", "benchmark_50k", "em171"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "orb_v1.json").write_text(json.dumps([entry("orb_v1")]))
    (root / "benchmark_50k" / "orb_v1.json").write_text(json.dumps([entry("orb_v1")]))
    (root / "em171" / "refresh_plan.json").write_text(
        json.dumps([entry("orb_v1"), entry("rsi_pullback_v1")])
    )
    (root / "em171" / "liquidity_thrust_v1.json").write_text(
        json.dumps([entry("liquidity_thrust_v1")])
    )
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "reports")
    return root


class TestSources:
    def test_every_published_entry_is_a_source_with_its_own_tag(self, strategies: Path) -> None:
        sources = BackfillSources(GitRepository(strategies.parent.parent)).discover(strategies)

        found = sorted((s.document["strategy"], s.tag) for s in sources)
        assert found == [
            ("liquidity_thrust_v1", "em171"),
            ("orb_v1", "benchmark-50k"),
            ("orb_v1", "em171-refresh"),
            ("orb_v1", "first-curation"),
            ("rsi_pullback_v1", "em171-refresh"),
        ]

    def test_each_source_is_dated_by_when_its_file_was_first_committed(
        self, strategies: Path
    ) -> None:
        [source, *_] = BackfillSources(GitRepository(strategies.parent.parent)).discover(strategies)

        assert source.first_committed.tzinfo is not None
        assert abs((datetime.now(UTC) - source.first_committed).total_seconds()) < 3600

    def test_a_file_that_was_never_committed_cannot_be_dated_and_is_refused(
        self, strategies: Path
    ) -> None:
        (strategies / "vwap_reversion_v1.json").write_text(json.dumps([entry("vwap_reversion_v1")]))

        with pytest.raises(ConfigurationError, match="never committed"):
            BackfillSources(GitRepository(strategies.parent.parent)).discover(strategies)


class TestBackfillCurations:
    def registry(self, tmp_path: Path) -> FileExperimentRegistry:
        return FileExperimentRegistry(tmp_path / "experiments")

    def test_every_source_is_published_and_indexed(self, strategies: Path, tmp_path: Path) -> None:
        sources = BackfillSources(GitRepository(strategies.parent.parent)).discover(strategies)
        backfill = ExperimentBackfill(self.registry(tmp_path), ReportBackfill(GATE_CODES))

        summary = backfill.curation_reports(sources)

        assert (summary.written, summary.unchanged, summary.indexed) == (5, 0, 5)
        rows = json.loads((tmp_path / "experiments" / INDEX_JSON).read_text())["experiments"]
        assert len(rows) == 5 and {r["predeclared"] for r in rows} == {False}
        assert {r["outcome"] for r in rows} == {"rejected"}

    def test_running_it_again_changes_nothing(self, strategies: Path, tmp_path: Path) -> None:
        sources = BackfillSources(GitRepository(strategies.parent.parent)).discover(strategies)
        backfill = ExperimentBackfill(self.registry(tmp_path), ReportBackfill(GATE_CODES))
        backfill.curation_reports(sources)

        summary = backfill.curation_reports(sources)

        assert (summary.written, summary.unchanged) == (0, 5)


class TestBackfillLedgers:
    def test_a_hypothesis_with_only_a_holdout_of_thin_evidence_is_published_inconclusive(
        self, tmp_path: Path
    ) -> None:
        hypothesis = HypothesisDeclaration(
            "em179-residual",
            "residual_momentum",
            "v1",
            datetime(2025, 9, 22).date(),
            datetime(2026, 9, 18).date(),
            datetime(2026, 7, 1).date(),
            datetime(2026, 9, 18).date(),
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        trial = FeatureTrial(
            "t1", "em179-residual", "residual_momentum", "v1", "30m", TrialRole.TEST, "d", "c",
            None, None, 30, Decimal("0.001"), Decimal("0.0004"), Decimal("0.5"), None, None,
            Decimal("2.5"), datetime(2026, 9, 10, tzinfo=UTC),
        )  # fmt: skip
        experiment = FeatureLedgerEvidence.of(hypothesis, [trial])
        registry = FileExperimentRegistry(tmp_path / "experiments")
        backfill = ExperimentBackfill(registry, ReportBackfill(GATE_CODES))

        summary = backfill.ledger_reports([experiment])

        assert summary.written == 1
        [row] = json.loads((tmp_path / "experiments" / INDEX_JSON).read_text())["experiments"]
        assert row["family"] == ExperimentFamily.FEATURE.value
        assert row["outcome"] == "inconclusive" and row["predeclared"] is False
