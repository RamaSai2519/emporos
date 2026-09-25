"""EM-228/229: the screen-swing command, end to end on a tiny synthetic universe."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from emporos.cli.experiment_commands import research_app
from emporos.cli.swing_commands import NIFTY_50
from emporos.persistence.candle_cache import CandleCacheFiles
from emporos.research.d1_universe import D1Manifest, LiquidityRule
from emporos.research.daily_bars import DailyBarStore
from emporos.research.partition import DISCOVERY
from tests.unit.research.swing.support import bar, sessions

RUNNER = CliRunner()
DAYS = sessions(420, date(2018, 1, 1))


def rising(instrument: str, start: int, step: float) -> list:  # type: ignore[type-arg]
    out, price = [], Decimal(start)
    for d in DAYS:
        nxt = (price * (1 + Decimal(str(step)))).quantize(Decimal("0.01"))
        out.append(bar(instrument, d, str(price), str(nxt)))
        price = nxt
    return out


def world(tmp_path: Path, *, factors: bool = True, missing_bars: bool = False) -> list[str]:
    root = tmp_path / "derived"
    store = DailyBarStore(CandleCacheFiles(root))
    store.write("NSE:1", rising("NSE:1", 100, 0.002))
    if not missing_bars:
        store.write("NSE:2", rising("NSE:2", 300, 0.001))
    store.write(NIFTY_50, rising(NIFTY_50, 10000, 0.0008))
    D1Manifest(
        "seed", Decimal("0.3"), LiquidityRule(), DISCOVERY, (), ("NSE:99",), ("NSE:1", "NSE:2"),
        {}, (),
    ).save(tmp_path / "manifest.yaml")  # fmt: skip
    (tmp_path / "tokens.csv").write_text("Symbol,Token\nAAA,1\nBBB,2\n", encoding="utf-8")
    (tmp_path / "adjustments.yaml").write_text(
        "factors:\n- {instrument_id: 'NSE:1', ex_date: 2015-01-01, ratio: '0.5', kind: split, "
        "source: test}\n" if factors else "factors: []\n",
        encoding="utf-8",
    )  # fmt: skip
    (tmp_path / "events").mkdir()
    return [
        "screen-swing", "--manifest", str(tmp_path / "manifest.yaml"),
        "--tokens", str(tmp_path / "tokens.csv"),
        "--adjustments", str(tmp_path / "adjustments.yaml"),
        "--ledger", str(tmp_path / "screens.jsonl"), "--pnl-dir", str(tmp_path / "pnl"),
        "--events", str(tmp_path / "events"), "--root", str(root), "--bootstrap-paths", "20",
    ]  # fmt: skip


def test_it_screens_every_arm_and_records_each_once(tmp_path: Path) -> None:
    args = world(tmp_path)

    result = RUNNER.invoke(research_app, [args[0], "a1-momentum-trend-filter", *args[1:]])

    assert result.exit_code == 0, result.output
    lines = (tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8
    assert {json.loads(line)["max_positions"] for line in lines} == {3, 5}
    assert len(list((tmp_path / "pnl").glob("*.csv"))) == 8
    assert "benchmark (same-universe equal-weight buy-and-hold" in result.output
    assert "net return per calendar year" in result.output
    assert "P(drawdown>=30%)" in result.output
    assert "regime defined from" in result.output
    assert "REAL gaps a held position went through" in result.output
    assert "real (traded through)" in result.output


def test_running_it_again_does_not_add_looks(tmp_path: Path) -> None:
    args = world(tmp_path)
    invoke = [args[0], "a1-momentum-trend-filter", *args[1:]]
    RUNNER.invoke(research_app, invoke)

    again = RUNNER.invoke(research_app, invoke)

    assert again.exit_code == 0
    assert len((tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()) == 8
    assert "already in the ledger" in again.output


def test_it_refuses_while_the_adjustment_ledger_is_empty(tmp_path: Path) -> None:
    args = world(tmp_path, factors=False)

    result = RUNNER.invoke(research_app, [args[0], "a1-momentum-trend-filter", *args[1:]])

    assert result.exit_code == 1
    assert "PROFIT_PLAN §2.6" in result.output
    assert not (tmp_path / "screens.jsonl").exists()


def test_it_refuses_when_a_name_has_no_daily_bars(tmp_path: Path) -> None:
    args = world(tmp_path, missing_bars=True)

    result = RUNNER.invoke(research_app, [args[0], "a1-momentum-trend-filter", *args[1:]])

    assert result.exit_code == 1
    assert "no daily bars for 1 names" in result.output


def test_an_unknown_cell_is_refused(tmp_path: Path) -> None:
    args = world(tmp_path)

    result = RUNNER.invoke(research_app, [args[0], "l3-raw-gap", *args[1:]])

    assert result.exit_code == 1
    assert "no swing cell" in result.output


def test_a2_maps_results_events_to_reaction_sessions_and_reports_them_per_year(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from emporos.core.clock import IST
    from emporos.research.results_filings import FilingLedger, ResultsFiling

    args = world(tmp_path)
    filing = ResultsFiling(
        "AAA", "INE0", datetime(2019, 5, 6, 17, 30, tzinfo=IST), "1", "results", "f"
    )
    FilingLedger(
        tmp_path / "events" / "results-filings.jsonl",
        tmp_path / "events" / "results-collected.jsonl",
    ).record(
        "AAA", [filing], date(2016, 10, 3), date(2024, 12, 31), datetime(2026, 9, 25, tzinfo=UTC)
    )

    result = RUNNER.invoke(research_app, [args[0], "a2-post-earnings-drift", *args[1:]])

    assert result.exit_code == 0, result.output
    assert "reaction sessions used per year: {2019: 1}" in result.output
    assert len((tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()) == 8
