"""EM-233: the screen-book command (A4), end to end on a tiny synthetic stock and ETF world."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from emporos.cli.experiment_commands import research_app
from emporos.cli.swing_worlds import NIFTY_50
from emporos.persistence.candle_cache import CandleCacheFiles
from emporos.research.d1_universe import D1Manifest, LiquidityRule
from emporos.research.daily_bars import DailyBarStore
from emporos.research.partition import DISCOVERY
from tests.unit.cli.test_rotation_commands import DAYS, ETFS, path, write_cold

RUNNER = CliRunner()
START = DAYS[400]


def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    cold = tmp_path / "cold"
    for instrument, rate in zip(ETFS.values(), ("0.0008", "0.0004", "0.0002"), strict=True):
        write_cold(cold, path(instrument, rate))
    monkeypatch.setenv("COLD_ARCHIVE_DIR", str(cold))
    root = tmp_path / "derived"
    store = DailyBarStore(CandleCacheFiles(root))
    store.write("NSE:1", path("NSE:1", "0.0010"))
    store.write("NSE:2", path("NSE:2", "0.0006"))
    store.write(NIFTY_50, path(NIFTY_50, "0.0005"))
    D1Manifest(
        "seed", Decimal("0.3"), LiquidityRule(), DISCOVERY, (), ("NSE:99",), ("NSE:1", "NSE:2"),
        {}, (),
    ).save(tmp_path / "manifest.yaml")  # fmt: skip
    (tmp_path / "adjustments.yaml").write_text(
        "factors:\n- {instrument_id: 'NSE:1', ex_date: 2015-01-01, ratio: '0.5', kind: split, "
        "source: test}\n",
        encoding="utf-8",
    )  # fmt: skip
    report = tmp_path / "etf-report.yaml"
    report.write_text(
        yaml.safe_dump(
            {
                "etfs": [
                    {"symbol": s, "instrument_id": i, "first_day": DAYS[0].isoformat()}
                    for s, i in ETFS.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    return [
        "screen-book", "a4-momentum-rotation-book", "--manifest", str(tmp_path / "manifest.yaml"),
        "--adjustments", str(tmp_path / "adjustments.yaml"), "--etf-report", str(report),
        "--ledger", str(tmp_path / "screens.jsonl"), "--pnl-dir", str(tmp_path / "pnl"),
        "--root", str(root), "--bootstrap-paths", "20", "--first-day", START.isoformat(),
    ]  # fmt: skip


def test_it_screens_both_splits_and_reports_the_book_what_it_is_made_of_and_its_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = world(tmp_path, monkeypatch)

    result = RUNNER.invoke(research_app, args)

    assert result.exit_code == 0, result.output
    lines = (tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["parameters"]["split"] for line in lines} == {"60/40", "50/50"}
    assert {json.loads(line)["max_positions"] for line in lines} == {7}
    assert len(list((tmp_path / "pnl").glob("*.csv"))) == 2
    out = result.output
    assert "SLEEVE level" in out
    assert "single stocks" in out and "exempt" in out
    assert "monthly correlation, equity vs defensive sleeve" in out
    assert "quarterly resets moved" in out
    assert "each ETF's share of the book's net trade profit" in out
    assert "sub-period from 2018-01-01" in out
    assert "BENCHMARK blend 60/40" in out
    assert "equity: " in out and "defensive: " in out
    assert "halts [" in out


def test_running_it_again_does_not_add_looks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = world(tmp_path, monkeypatch)
    RUNNER.invoke(research_app, args)

    again = RUNNER.invoke(research_app, args)

    assert again.exit_code == 0
    assert len((tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert "already in the ledger" in again.output


def test_a_start_before_the_etfs_can_be_judged_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = world(tmp_path, monkeypatch)
    args[-1] = date(2016, 2, 1).isoformat()

    result = RUNNER.invoke(research_app, args)

    assert result.exit_code == 1
    assert "are judged from" in result.output


def test_an_unknown_book_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = world(tmp_path, monkeypatch)
    args[1] = "a5-etf-dual-momentum"

    result = RUNNER.invoke(research_app, args)

    assert result.exit_code == 1
    assert "no book cell" in result.output
