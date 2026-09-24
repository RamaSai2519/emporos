"""EM-188: every family renders the same sections and the same metric rows, in the same order."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal

from emporos.backtest.experiment_document import NOT_APPLICABLE, ExperimentDocument
from emporos.domain.research_experiments import (
    ExperimentFamily,
    ExperimentMetrics,
    ExperimentPeriods,
    VersionStamp,
)
from tests.support.experiment_reports import sample_report

SECTIONS = [
    "Declaration (made before the run)",
    "Versions",
    "Periods",
    "Headline metrics",
    "Cost breakdown",
    "By regime",
    "By walk-forward window",
    "Reasons",
    "Notes",
]


def headings(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, flags=re.MULTILINE)


def metric_rows(text: str) -> list[str]:
    block = text.split("## Headline metrics")[1].split("## Cost breakdown")[0]
    return [line.split("|")[1].strip() for line in block.splitlines() if line.startswith("| ")][1:]


class TestMarkdown:
    def test_sections_come_in_a_fixed_order(self) -> None:
        assert headings(ExperimentDocument().markdown(sample_report())) == SECTIONS

    def test_an_empty_report_has_the_same_sections_and_metric_rows(self) -> None:
        empty = replace(
            sample_report(family=ExperimentFamily.FEATURE),
            metrics=ExperimentMetrics(),
            versions=VersionStamp(),
            periods=ExperimentPeriods(),
            notes=(),
            reasons=(),
        )

        full_text = ExperimentDocument().markdown(sample_report())
        empty_text = ExperimentDocument().markdown(empty)

        assert headings(empty_text) == SECTIONS
        assert metric_rows(empty_text) == metric_rows(full_text)

    def test_what_does_not_apply_reads_n_a_not_zero(self) -> None:
        empty = replace(sample_report(), metrics=ExperimentMetrics())

        text = ExperimentDocument().markdown(empty)

        assert f"| net P&L | {NOT_APPLICABLE} |" in text
        assert f"| trades | {NOT_APPLICABLE} |" in text

    def test_it_states_the_id_outcome_and_declaration(self) -> None:
        report = sample_report()

        text = ExperimentDocument().markdown(report)

        assert text.startswith(f"# {report.experiment_id}\n")
        assert "**INCONCLUSIVE**" in text
        assert report.declaration.economic_rationale in text

    def test_a_pipe_in_a_cell_cannot_break_the_table(self) -> None:
        report = replace(
            sample_report(),
            reasons=(replace(sample_report().reasons[0], detail="a | b"),),
        )

        assert "a \\| b" in ExperimentDocument().markdown(report)


class TestJson:
    def test_it_is_plain_json_with_every_number_exact(self) -> None:
        document = ExperimentDocument().to_json(sample_report())

        text = json.dumps(document)

        assert json.loads(text) == document
        assert document["metrics"]["net_pnl"] == "3120.75"
        assert document["metrics"]["trade_count"] == 360

    def test_no_float_appears_anywhere(self) -> None:
        def walk(value: object) -> None:
            assert not isinstance(value, float)
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(ExperimentDocument().to_json(sample_report()))

    def test_the_top_level_keys_are_in_a_fixed_order(self) -> None:
        keys = list(ExperimentDocument().to_json(sample_report()))

        assert keys == [
            "schema_version", "experiment_id", "family", "slug", "outcome", "declaration",
            "versions", "periods", "metrics", "reasons", "primary_reason_codes", "notes",
            "supporting",
        ]  # fmt: skip

    def test_primary_reason_codes_name_what_decided_the_outcome(self) -> None:
        document = ExperimentDocument().to_json(sample_report())

        assert document["primary_reason_codes"] == ["dsr_below_threshold"]

    def test_the_same_report_renders_identically_twice(self) -> None:
        a = json.dumps(ExperimentDocument().to_json(sample_report()), indent=2)
        b = json.dumps(ExperimentDocument().to_json(sample_report()), indent=2)

        assert a == b


class TestDeclaredSize:
    """EM-191 F4: the declared position value is part of the rendered declaration."""

    def test_a_declared_value_is_in_both_renderings(self) -> None:
        report = sample_report()
        sized = replace(
            report, declaration=replace(report.declaration, position_value=Decimal(25000))
        )

        document = ExperimentDocument()

        assert document.to_json(sized)["declaration"]["position_value"] == "25000"
        assert "Declared position value: ₹25,000.00" in document.markdown(sized)

    def test_an_undeclared_value_adds_nothing_so_older_reports_render_as_they_did(self) -> None:
        document = ExperimentDocument()

        assert "position_value" not in document.to_json(sample_report())["declaration"]
        assert "position value" not in document.markdown(sample_report())
