"""Backfilling the experiment registry from evidence that predates it (EM-188).

Two kinds of source, both read-only and both marked BACKFILLED_NOT_PREDECLARED by their builders:
the published curation JSON reports under docs/strategies, and the feature-research ledgers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from emporos.backtest.experiment_backfill import BackfillSource, ReportBackfill
from emporos.cli.experiment_provenance import GitRepository
from emporos.cli.experiment_registry import ExperimentRegistry, PublishOutcome
from emporos.core.errors import ConfigurationError
from emporos.domain.research_experiments import ExperimentReport, VersionStamp
from emporos.research.experiment_report import (
    BackfilledDeclaration,
    LedgerExperiment,
    LedgerExperimentReportBuilder,
)

DEFAULT_STRATEGIES_DIR = Path("docs/strategies")


class BackfillSources:
    """Finds the published curation reports and dates each by when it entered the repository."""

    _ROOT_TAG = "first-curation"

    def __init__(self, git: GitRepository) -> None:
        self._git = git

    def discover(self, strategies_dir: Path) -> list[BackfillSource]:
        sources: list[BackfillSource] = []
        for pattern in ("*.json", "benchmark_50k/*.json", "em171/*.json"):
            for path in sorted(strategies_dir.glob(pattern)):
                sources += self._entries(path, self._tag(strategies_dir, path))
        return sources

    def _entries(self, path: Path, tag: str) -> list[BackfillSource]:
        committed = self._git.first_commit_time(path)
        if committed is None:
            raise ConfigurationError(f"{path} was never committed, so its report cannot be dated")
        documents = json.loads(path.read_text(encoding="utf-8"))
        return [BackfillSource(d, tag, path.as_posix(), committed) for d in documents]

    def _tag(self, strategies_dir: Path, path: Path) -> str:
        if path.parent == strategies_dir:
            return self._ROOT_TAG
        tag = path.parent.name.replace("_", "-")
        return f"{tag}-refresh" if path.stem.startswith("refresh_") else tag


@dataclass(frozen=True)
class BackfillSummary:
    written: int
    unchanged: int
    indexed: int


class ExperimentBackfill:
    def __init__(self, registry: ExperimentRegistry, backfill: ReportBackfill) -> None:
        self._registry = registry
        self._backfill = backfill

    def curation_reports(self, sources: Sequence[BackfillSource]) -> BackfillSummary:
        reports = [
            self._backfill.build(s, self._backfill.declaration(s), VersionStamp()) for s in sources
        ]
        return self._publish(reports)

    def ledger_reports(self, experiments: Sequence[LedgerExperiment]) -> BackfillSummary:
        builder = LedgerExperimentReportBuilder()
        reports = [
            builder.build(e, BackfilledDeclaration.of(e.family, e.hypothesis), VersionStamp())
            for e in experiments
        ]
        return self._publish(reports)

    def _publish(self, reports: Sequence[ExperimentReport]) -> BackfillSummary:
        written = unchanged = 0
        for report in reports:
            outcome = self._registry.publish(report)
            written += outcome is PublishOutcome.WRITTEN
            unchanged += outcome is PublishOutcome.UNCHANGED
        return BackfillSummary(written, unchanged, self._registry.rebuild_index())
