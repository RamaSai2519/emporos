"""Golden-file regression for the backtest (plan.md §18: any metric drift fails the build).

Two goldens, two jobs:

* `momentum_v1_real_year.golden.json` — momentum_v1 over ONE YEAR of real 5m bars (RELIANCE, TCS,
  2025-09-22 .. 2026-09-18) from the committed fixture, the shipped YAML and the shipped fee
  schedule. The Phase 10 acceptance run.
* `momentum_wave_engine.golden.json` — momentum_v1 over a small deterministic wave: fast, and
  independent of the fixture, so an engine change shows up even if the data file is untouched.

If a change to the numbers is INTENDED, regenerate deliberately and review the diff:
`python -m tests.regression.regenerate_goldens` (add `--write` to replace the files).
"""

from __future__ import annotations

import json

import pytest

from tests.regression.goldens import (
    ACCEPTANCE_REPORT,
    REAL_YEAR,
    WAVE,
    real_year_document,
    render,
    wave_document,
)
from tests.support.backtest_real import FixtureCandleReader, manifest, sha256_of_bars

pytestmark = pytest.mark.regression


def test_the_committed_bars_are_the_bars_the_manifest_pins() -> None:
    assert sha256_of_bars() == manifest()["sha256"]
    assert FixtureCandleReader().total_bars == manifest()["bars"] == 36488


async def test_the_real_year_backtest_matches_its_golden_file() -> None:
    actual = await real_year_document()

    assert actual == json.loads(REAL_YEAR.read_text(encoding="utf-8"))


async def test_the_wave_backtest_matches_its_golden_file() -> None:
    actual = await wave_document()

    assert actual == json.loads(WAVE.read_text(encoding="utf-8"))


async def test_the_golden_files_are_written_exactly_as_the_regenerator_writes_them() -> None:
    """Text-level: a hand edit that keeps the JSON equal but reformats it is still noticed."""
    assert render(await real_year_document()) == REAL_YEAR.read_text(encoding="utf-8")
    assert render(await wave_document()) == WAVE.read_text(encoding="utf-8")


def test_the_documented_acceptance_report_is_the_golden() -> None:
    """`docs/backtests/…json` is what `emporos backtest run` wrote against Atlas; the golden is what
    the committed fixture produces. They must be the same document: the frozen data really is the
    data the reported run used."""
    assert json.loads(ACCEPTANCE_REPORT.read_text()) == json.loads(REAL_YEAR.read_text())


async def test_the_real_year_result_is_a_full_year_with_a_full_metrics_report() -> None:
    doc = await real_year_document()

    metrics = doc["metrics"]
    assert doc["run"]["first_trading_day"] == "2025-09-22"
    assert metrics["account"]["trading_days"] == 245
    assert metrics["trades"]["count"] > 100  # a year of trading, not a handful of trades
    for section, keys in {
        "returns": ["total_return", "cagr", "sharpe", "sortino", "calmar"],
        "drawdown": ["max_drawdown", "worst_periods", "longest_days"],
        "trades": ["win_rate", "profit_factor", "expectancy", "average_trade",
                   "max_consecutive_wins", "max_consecutive_losses"],
        "exposure": ["time_in_market", "average_exposure"],
        "turnover": ["turnover", "annualised_turnover"],
    }.items():  # fmt: skip
        assert all(metrics[section][key] is not None for key in keys), section
    assert len(metrics["monthly_returns"]) == 13  # Sep 2025 .. Sep 2026
    assert any("NO RISK ENGINE" in note for note in doc["assumptions"])
