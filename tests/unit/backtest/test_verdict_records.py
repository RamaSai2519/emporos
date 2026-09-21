"""Curation results and curation reports become the same recorded verdict."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from emporos.backtest.verdict_records import ReportVerdicts, VerdictContext
from emporos.domain.experiments import Verdict

CONTEXT = VerdictContext(
    behaviour_hash="sha256:abc",
    capital="50000",
    first_day="2025-09-22",
    last_day="2026-09-18",
    experiment="em118",
    recorded_at=datetime(2026, 9, 21, tzinfo=UTC),
)


def test_a_shipped_report_becomes_a_rejected_verdict_with_its_gates() -> None:
    documents = json.loads(Path("docs/strategies/benchmark_50k/orb_v1.json").read_text())
    (document,) = documents

    verdict = ReportVerdicts().of(document, CONTEXT)

    assert verdict.strategy == "orb_v1" and verdict.verdict is Verdict.REJECTED
    assert verdict.source == "imported" and verdict.behaviour_hash == "sha256:abc"
    assert [g.name for g in verdict.failing]  # the reasons travel with the verdict
    assert {g.outcome for g in verdict.gates} <= {"pass", "fail", "unknown"}
    assert len(verdict.gates) == len(document["robustness"]["gates"])


def test_a_report_without_an_assessment_can_never_be_validated() -> None:
    passed = {"strategy": "s", "passed": True, "checks": [
        {"name": "net profit", "passed": True, "actual": "10", "required": "> 0"}
    ]}  # fmt: skip
    failed = {**passed, "passed": False, "checks": [
        {"name": "net profit", "passed": False, "actual": "-5", "required": "> 0"}
    ]}  # fmt: skip

    assert ReportVerdicts().of(passed, CONTEXT).verdict is Verdict.INCONCLUSIVE
    assert ReportVerdicts().of(failed, CONTEXT).verdict is Verdict.REJECTED
    assert ReportVerdicts().of(failed, CONTEXT).failing[0].detail == "-5 (needs > 0)"


def test_entries_are_keyed_by_strategy_name() -> None:
    documents = [{"strategy": "a"}, {"strategy": "b"}]

    assert set(ReportVerdicts().entries(documents)) == {"a", "b"}
