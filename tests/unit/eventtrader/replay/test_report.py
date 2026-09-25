"""The Dev run's outputs: CSVs, the text report, the counted ledger line."""

from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from emporos.eventtrader.replay.control import ControlResult
from emporos.eventtrader.replay.metrics import LossBootstrap, Section125Bars, metrics_for
from emporos.eventtrader.replay.records import Scenario
from emporos.eventtrader.replay.report import (
    LlmLedger,
    VariantIdentity,
    VariantReport,
    render_report,
    write_daily_csv,
    write_trades_csv,
)
from tests.unit.eventtrader.replay.test_metrics import START, run, sessions, trade

D = Decimal
WHEN = datetime(2026, 9, 26, tzinfo=UTC)


def identity(variant: str = "v1_t60", prompt: str = "abc") -> VariantIdentity:
    return VariantIdentity(
        "l1-llm-event-trader", variant, date(2024, 1, 1), date(2024, 12, 31), prompt,
        ("openai/gpt-4o-mini",),
    )  # fmt: skip


def report(with_control: bool = True) -> VariantReport:
    result = run([trade(START, "AAA", 200), trade(START, "BBB", -50)], {START: 30})
    days = sessions(30)
    bench = metrics_for(result, Scenario.BENCHMARK, days)
    adverse = metrics_for(result, Scenario.ADVERSE, days)
    p_loss = LossBootstrap(paths=200).p_loss([v for _, v in bench.daily])
    control = ControlResult(10, bench.net_total, (D(-5),) * 10, Scenario.BENCHMARK)
    return VariantReport(
        identity(), result, bench, adverse, p_loss,
        tuple(Section125Bars().evaluate(bench, adverse, p_loss, control.p_value)),
        control if with_control else None, bench, "median stop 2.0%, target 4.0%",
    )  # fmt: skip


def test_the_trades_csv_has_one_row_a_trade_with_both_scenarios(tmp_path: Path) -> None:
    path = tmp_path / "out" / "trades.csv"
    write_trades_csv(path, report().result.trades)

    rows = list(csv.DictReader(path.open()))
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]
    assert (rows[0]["net_benchmark"], rows[0]["net_adverse"]) == ("200", "190")


def test_the_daily_csv_carries_both_scenarios(tmp_path: Path) -> None:
    r = report()
    path = tmp_path / "daily.csv"
    write_daily_csv(path, r.benchmark, r.adverse)

    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 30 and rows[0]["day"] == START.isoformat()
    assert D(rows[0]["net_benchmark"]) == D(120)  # 150 net of costs minus 30 tokens


def test_the_text_report_shows_every_bar_the_control_and_the_baseline() -> None:
    text = render_report(report())

    assert "l1-llm-event-trader / v1_t60" in text
    assert text.count("[PASS]") + text.count("[FAIL]") == 9
    assert "coin-flip control" in text and "p = 0.0909" in text
    assert "DIAGNOSTIC, never a gate" in text and "median stop 2.0%" in text


def test_a_variant_is_one_counted_line_and_running_it_again_is_the_same_look(
    tmp_path: Path,
) -> None:
    ledger = LlmLedger(tmp_path / "screens.jsonl")

    assert ledger.append(report(), WHEN, {"trades": "t.csv"}) is True
    assert ledger.append(report(with_control=False), WHEN, {}) is False  # the same identity
    other = VariantReport(**{**report().__dict__, "identity": identity("v2_t75")})
    assert ledger.append(other, WHEN, {}) is True

    lines = [json.loads(x) for x in (tmp_path / "screens.jsonl").read_text().splitlines()]
    assert [x["variant"] for x in lines] == ["v1_t60", "v2_t75"]
    assert lines[0]["track"] == "llm" and lines[0]["screen_id"].startswith("LLM-")


def test_a_changed_prompt_is_a_different_look() -> None:
    assert identity(prompt="abc").screen_id != identity(prompt="abd").screen_id
