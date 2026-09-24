"""The backtest goldens: where they live, what produces them, how they are rendered.

Shared by the regression tests (which COMPARE) and `regenerate_goldens` (which, only when told to,
WRITES). One source of truth for "what the current code says the golden should be"."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from emporos.backtest.document import BacktestDocument
from emporos.backtest.experiment_document import ExperimentDocument
from tests.support.backtest_momentum import momentum_backtest
from tests.support.backtest_real import FIXTURES, real_year_backtest
from tests.support.experiment_reports import sample_report

REAL_YEAR = FIXTURES / "momentum_v1_real_year.golden.json"
WAVE = FIXTURES / "momentum_wave_engine.golden.json"
ACCEPTANCE_REPORT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "backtests"
    / "momentum_v1_2025-09-22_2026-09-18.json"
)


async def real_year_document() -> dict[str, Any]:
    return BacktestDocument().render(await real_year_backtest())


async def wave_document() -> dict[str, Any]:
    return BacktestDocument().render(await momentum_backtest())


EXPERIMENT_JSON = FIXTURES.parent / "experiments" / "sample_report.golden.json"
EXPERIMENT_MARKDOWN = FIXTURES.parent / "experiments" / "sample_report.golden.md"


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


def _rendered(produce: Callable[[], Awaitable[dict[str, Any]]]) -> Callable[[], Awaitable[str]]:
    async def go() -> str:
        return render(await produce())

    return go


async def experiment_report_json() -> str:
    """EM-188: the standard report for a fixed sample: a schema drift fails the build."""
    return render(ExperimentDocument().to_json(sample_report()))


async def experiment_report_markdown() -> str:
    return ExperimentDocument().markdown(sample_report())


# What each golden file should currently contain, as text: the regression tests COMPARE against
# these, `regenerate_goldens` WRITES them, only when told to.
GOLDENS: dict[Path, Callable[[], Awaitable[str]]] = {
    REAL_YEAR: _rendered(real_year_document),
    WAVE: _rendered(wave_document),
    EXPERIMENT_JSON: experiment_report_json,
    EXPERIMENT_MARKDOWN: experiment_report_markdown,
}
