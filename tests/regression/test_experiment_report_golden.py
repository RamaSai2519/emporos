"""EM-188: the standard experiment report for a fixed sample must not change by accident.

If this fails after an intended schema change, read the diff, explain it in the commit, and
regenerate DELIBERATELY: `python -m tests.regression.regenerate_goldens --write`."""

from __future__ import annotations

import json

from emporos.backtest.experiment_document import ExperimentDocument
from tests.regression.goldens import (
    EXPERIMENT_JSON,
    EXPERIMENT_MARKDOWN,
    experiment_report_json,
    experiment_report_markdown,
)
from tests.support.experiment_reports import sample_report


async def test_the_rendered_json_is_the_golden_byte_for_byte() -> None:
    assert await experiment_report_json() == EXPERIMENT_JSON.read_text(encoding="utf-8")


async def test_the_rendered_markdown_is_the_golden_byte_for_byte() -> None:
    assert await experiment_report_markdown() == EXPERIMENT_MARKDOWN.read_text(encoding="utf-8")


def test_the_json_golden_parses_to_the_current_document() -> None:
    golden = json.loads(EXPERIMENT_JSON.read_text(encoding="utf-8"))

    assert golden == ExperimentDocument().to_json(sample_report())
