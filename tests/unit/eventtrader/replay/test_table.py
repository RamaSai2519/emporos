"""The variants side by side and the declared rule for the best one."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from emporos.eventtrader.replay.table import (
    best_variant,
    load_summaries,
    render_table,
    summary_of,
)
from tests.unit.eventtrader.replay.test_report import report


def summary(variant: str, trades: int, expectancy_adverse: float | None) -> dict[str, Any]:
    s = summary_of(report())
    return {**s, "variant": variant, "trades": trades, "expectancy_adverse": expectancy_adverse}


def test_a_summary_carries_the_numbers_the_table_and_the_bars_need() -> None:
    s = summary_of(report())

    assert s["variant"] == "v1_t60" and s["trades"] == 2 and len(s["bars"]) == 9
    assert s["trades_by_name"] == {"AAA": 1, "BBB": 1} and s["control_p"] is not None
    assert json.loads(json.dumps(s)) == s  # plain JSON


def test_the_best_variant_is_the_highest_adverse_expectancy_among_those_with_30_trades() -> None:
    table = [
        summary("v1", 29, 900.0),  # too few trades
        summary("v2", 30, 12.0),
        summary("v3", 80, 15.5),
        summary("v4", 200, None),
    ]

    assert best_variant(table) == "v3"
    assert best_variant([summary("v1", 29, 900.0)]) is None


def test_the_table_has_one_column_a_variant_and_every_section() -> None:
    text = render_table([summary("v1_t60", 40, 5.0), summary("v2_t75", 35, 7.0)])

    assert "v1_t60" in text and "v2_t75" in text and "coin-flip p" in text
    assert "refusals by reason" in text and "trades per instrument" in text
    assert "best by the declared rule" in text and "v2_t75" in text.splitlines()[-1]
    assert render_table([]) == "no variant summaries\n"


def test_summaries_are_read_back_from_a_directory(tmp_path: Path) -> None:
    (tmp_path / "v1_t60-summary.json").write_text(json.dumps(summary_of(report())))
    (tmp_path / "v1_t60-report.txt").write_text("not a summary")

    assert [s["variant"] for s in load_summaries(tmp_path)] == ["v1_t60"]
