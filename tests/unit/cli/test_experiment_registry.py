"""EM-188: the file registry is write-once, idempotent, and its index is ordered and honest."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from emporos.backtest.experiment_document import NOT_APPLICABLE
from emporos.cli.experiment_registry import (
    INDEX_JSON,
    INDEX_MARKDOWN,
    ExperimentAlreadyPublishedError,
    FileExperimentRegistry,
    PublishOutcome,
)
from emporos.domain.research_experiments import ExperimentFamily, ExperimentMetrics
from tests.support.experiment_reports import another_report, sample_report


def at(day: int) -> datetime:
    return datetime(2026, 9, day, 3, 0, tzinfo=UTC)


class TestPublish:
    def test_it_writes_the_json_record_and_the_markdown(self, tmp_path: Path) -> None:
        report = sample_report()

        outcome = FileExperimentRegistry(tmp_path).publish(report)

        assert outcome is PublishOutcome.WRITTEN
        assert json.loads((tmp_path / f"{report.experiment_id}.json").read_text())[
            "experiment_id"
        ] == str(report.experiment_id)
        assert (tmp_path / f"{report.experiment_id}.md").read_text().startswith("# EXP-")

    def test_it_creates_the_directory(self, tmp_path: Path) -> None:
        FileExperimentRegistry(tmp_path / "deep" / "experiments").publish(sample_report())

        assert (tmp_path / "deep" / "experiments").is_dir()

    def test_publishing_the_same_report_again_is_a_no_op(self, tmp_path: Path) -> None:
        registry = FileExperimentRegistry(tmp_path)
        registry.publish(sample_report())
        record = tmp_path / f"{sample_report().experiment_id}.json"
        before = record.stat().st_mtime_ns

        outcome = registry.publish(sample_report())

        assert outcome is PublishOutcome.UNCHANGED
        assert record.stat().st_mtime_ns == before

    def test_a_different_report_under_the_same_id_is_refused_and_leaves_the_record(
        self, tmp_path: Path
    ) -> None:
        registry = FileExperimentRegistry(tmp_path)
        original = sample_report()
        registry.publish(original)
        record = tmp_path / f"{original.experiment_id}.json"
        text = record.read_text()
        changed = replace(original, metrics=ExperimentMetrics(trade_count=1))

        with pytest.raises(ExperimentAlreadyPublishedError, match="immutable"):
            registry.publish(changed)

        assert record.read_text() == text

    def test_a_missing_markdown_is_restored_without_touching_the_record(
        self, tmp_path: Path
    ) -> None:
        registry = FileExperimentRegistry(tmp_path)
        report = sample_report()
        registry.publish(report)
        (tmp_path / f"{report.experiment_id}.md").unlink()

        outcome = registry.publish(report)

        assert outcome is PublishOutcome.UNCHANGED
        assert (tmp_path / f"{report.experiment_id}.md").exists()


class TestIndex:
    def publish_all(self, tmp_path: Path) -> FileExperimentRegistry:
        registry = FileExperimentRegistry(tmp_path)
        registry.publish(another_report("zeta", at(24), ExperimentFamily.STRATEGY))
        registry.publish(another_report("beta", at(20), ExperimentFamily.STRATEGY))
        registry.publish(another_report("alpha", at(25), ExperimentFamily.FEATURE))
        return registry

    def test_rows_are_sorted_by_family_then_declaration_time(self, tmp_path: Path) -> None:
        registry = self.publish_all(tmp_path)

        count = registry.rebuild_index()

        rows = json.loads((tmp_path / INDEX_JSON).read_text())["experiments"]
        assert count == 3
        assert [(r["family"], r["slug"]) for r in rows] == [
            ("feature", "alpha"),
            ("strategy", "beta"),
            ("strategy", "zeta"),
        ]

    def test_the_markdown_index_has_one_row_per_experiment_and_links_the_report(
        self, tmp_path: Path
    ) -> None:
        self.publish_all(tmp_path).rebuild_index()

        text = (tmp_path / INDEX_MARKDOWN).read_text()

        data_rows = [line for line in text.splitlines() if line.startswith("| [EXP-")]
        assert len(data_rows) == 3
        assert all(".md)" in row for row in data_rows)
        assert "| outcome |" in text and "| primary reasons |" in text

    def test_missing_numbers_read_n_a(self, tmp_path: Path) -> None:
        registry = FileExperimentRegistry(tmp_path)
        registry.publish(another_report("thin", at(24), ExperimentFamily.STRATEGY))
        registry.rebuild_index()

        row = next(
            line for line in (tmp_path / INDEX_MARKDOWN).read_text().splitlines() if "thin" in line
        )

        assert NOT_APPLICABLE in row

    def test_the_primary_reasons_are_the_deciding_rules(self, tmp_path: Path) -> None:
        registry = FileExperimentRegistry(tmp_path)
        registry.publish(sample_report())
        registry.rebuild_index()

        [row] = json.loads((tmp_path / INDEX_JSON).read_text())["experiments"]

        assert row["primary_reasons"] == ["dsr_below_threshold"]
        assert row["outcome"] == "inconclusive"

    def test_rebuilding_is_deterministic_and_ignores_its_own_output(self, tmp_path: Path) -> None:
        registry = self.publish_all(tmp_path)
        registry.rebuild_index()
        first = (tmp_path / INDEX_MARKDOWN).read_text(), (tmp_path / INDEX_JSON).read_text()

        registry.rebuild_index()

        assert (
            (tmp_path / INDEX_MARKDOWN).read_text(),
            (tmp_path / INDEX_JSON).read_text(),
        ) == first

    def test_an_empty_registry_has_an_empty_index(self, tmp_path: Path) -> None:
        assert FileExperimentRegistry(tmp_path / "none").rebuild_index() == 0
