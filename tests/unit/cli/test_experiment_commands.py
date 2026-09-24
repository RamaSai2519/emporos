"""EM-188: `emporos research experiments` and the curation publication step."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from emporos.backtest.experiment_report import CurationExperimentReportBuilder
from emporos.cli.experiment_registry import (
    INDEX_JSON,
    ExperimentPublication,
    FileExperimentRegistry,
    PublishOutcome,
)
from emporos.cli.main import app
from emporos.domain.research_experiments import ExperimentOutcomeLabel, VersionStamp
from emporos.domain.sizing import DeclaredSize, SizeSource
from tests.support.experiment_reports import sample_declaration, sample_report
from tests.unit.backtest.test_curation_run import curate, reservation

runner = CliRunner()
RISK_LIMIT = Decimal(25_000)
SIZE = DeclaredSize(RISK_LIMIT, SizeSource.RISK_DEFAULT)


class TestDeclare:
    def test_it_prints_the_id_the_declaration_will_publish_under(self) -> None:
        result = runner.invoke(
            app, ["research", "experiments", "declare", "config/experiments/orb-v1-ten-year.yaml"]
        )

        assert result.exit_code == 0
        assert "EXP-20260924-orb-v1-ten-year-" in result.output

    def test_an_invalid_declaration_fails_with_the_reason(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("slug: bad\nunknown_field: 1\n")

        result = runner.invoke(app, ["research", "experiments", "declare", str(bad)])

        assert result.exit_code == 1
        assert "declare failed" in result.output and "unknown key" in result.output


class TestIndex:
    def test_it_rebuilds_the_index_from_what_is_published(self, tmp_path: Path) -> None:
        FileExperimentRegistry(tmp_path).publish(sample_report())

        result = runner.invoke(app, ["research", "experiments", "index", "--root", str(tmp_path)])

        assert result.exit_code == 0 and "1 experiment(s) indexed" in result.output
        assert len(json.loads((tmp_path / INDEX_JSON).read_text())["experiments"]) == 1


class TestExperimentPublication:
    async def test_a_finished_curation_is_published_and_indexed(self, tmp_path: Path) -> None:
        record, _ = await curate(reservation())
        publication = ExperimentPublication(
            CurationExperimentReportBuilder(SIZE, RISK_LIMIT), FileExperimentRegistry(tmp_path)
        )

        report, outcome = publication.publish(record, sample_declaration(), VersionStamp())

        assert outcome is PublishOutcome.WRITTEN
        assert (tmp_path / f"{report.experiment_id}.json").exists()
        assert (tmp_path / f"{report.experiment_id}.md").exists()
        rows = json.loads((tmp_path / INDEX_JSON).read_text())["experiments"]
        assert [r["id"] for r in rows] == [str(report.experiment_id)]
        assert report.outcome is not ExperimentOutcomeLabel.ACCEPTED or report.periods.holdout

    async def test_publishing_the_same_curation_again_changes_nothing(self, tmp_path: Path) -> None:
        record, _ = await curate(reservation())
        publication = ExperimentPublication(
            CurationExperimentReportBuilder(SIZE, RISK_LIMIT), FileExperimentRegistry(tmp_path)
        )
        publication.publish(record, sample_declaration(), VersionStamp())

        _, outcome = publication.publish(record, sample_declaration(), VersionStamp())

        assert outcome is PublishOutcome.UNCHANGED

    async def test_the_published_report_has_the_holdout_and_every_version_stamp(
        self, tmp_path: Path
    ) -> None:
        record, _ = await curate(reservation())
        publication = ExperimentPublication(
            CurationExperimentReportBuilder(SIZE, RISK_LIMIT), FileExperimentRegistry(tmp_path)
        )

        report, _ = publication.publish(
            record, sample_declaration(), VersionStamp(code_revision="abc1234")
        )

        document = json.loads((tmp_path / f"{report.experiment_id}.json").read_text())
        assert document["periods"]["holdout"] is not None
        assert document["versions"]["dataset"] is not None
        assert document["versions"]["behaviour_hash"].startswith("sha256:")
        assert document["versions"]["code_revision"] == "abc1234"


class TestReport:
    def test_a_family_without_a_trial_ledger_is_refused_before_any_database_is_touched(
        self,
    ) -> None:
        result = runner.invoke(
            app, ["research", "experiments", "report", "--hypothesis", "h1", "--family", "strategy"]
        )

        assert result.exit_code == 1
        assert "report failed" in result.output and "not backed by a trial ledger" in result.output

    def test_an_uncommitted_declaration_is_refused_before_any_database_is_touched(
        self, tmp_path: Path
    ) -> None:
        declaration = tmp_path / "feat-test.yaml"
        declaration.write_text(
            "family: feature\nslug: feat-test\nhypothesis: h\neconomic_rationale: r\n"
            "falsification: f\ndeclared_at: 2026-09-24T09:00:00+05:30\n"
        )

        result = runner.invoke(
            app,
            ["research", "experiments", "report", "--hypothesis", "h1",
             "--declaration", str(declaration)],
        )  # fmt: skip

        assert result.exit_code == 1 and "not committed" in result.output
