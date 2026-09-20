"""The backtest goldens: where they live, what produces them, how they are rendered.

Shared by the regression tests (which COMPARE) and `regenerate_goldens` (which, only when told to,
WRITES). One source of truth for "what the current code says the golden should be"."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from emporos.backtest.document import BacktestDocument
from tests.support.backtest_momentum import momentum_backtest
from tests.support.backtest_real import FIXTURES, real_year_backtest

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


GOLDENS: dict[Path, Callable[[], Awaitable[dict[str, Any]]]] = {
    REAL_YEAR: real_year_document,
    WAVE: wave_document,
}


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"
