"""EM-237: the screen-core command end to end on synthetic ETFs."""

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
from emporos.domain.candles import Candle
from emporos.persistence.candle_cache import CandleCacheFiles
from emporos.research.daily_bars import DailyBarStore
from tests.unit.cli.test_rotation_commands import ETFS, write_cold
from tests.unit.research.swing.support import bar, sessions

RUNNER = CliRunner()
DAYS = sessions(1000, date(2015, 1, 5))


def path(instrument: str, rate: str) -> list[Candle]:
    price, out = Decimal(100), []
    for d in DAYS:
        nxt = (price * (1 + Decimal(rate))).quantize(Decimal("0.01"))
        out.append(bar(instrument, d, str(price), str(nxt)))
        price = nxt
    return out


def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    cold = tmp_path / "cold"
    for (_, instrument), rate in zip(ETFS.items(), ("0.0008", "0.0004", "0.0002"), strict=True):
        write_cold(cold, path(instrument, rate))
    derived = tmp_path / "derived"
    DailyBarStore(CandleCacheFiles(derived)).write(NIFTY_50, path(NIFTY_50, "0.0005"))
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
    monkeypatch.setenv("COLD_ARCHIVE_DIR", str(cold))
    return [
        "screen-core", "c1-etf-trend-core", "--etf-report", str(report),
        "--ledger", str(tmp_path / "screens.jsonl"), "--pnl-dir", str(tmp_path / "pnl"),
        "--root", str(derived), "--bootstrap-paths", "20",
    ]  # fmt: skip


def test_it_screens_the_three_arms_on_the_core_bar_and_reports_the_halves_and_the_slice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = world(tmp_path, monkeypatch)

    result = RUNNER.invoke(research_app, args)

    assert result.exit_code == 0, result.output
    lines = (tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["parameters"]["rule"] for line in lines} == {
        "top2", "all_equal", "fixed_40_30_30",
    }  # fmt: skip
    assert "273 prior sessions" in result.output
    assert "fills at the next close" in result.output
    assert "PROFIT_PLAN §10 core bar" in result.output
    assert "<=2016" in result.output and "2017+" in result.output
    assert "sub-period from 2017-11-13" in result.output
    assert "BENCHMARK 60/40" in result.output
    assert len(list((tmp_path / "pnl").glob("*.csv"))) == 3


def test_an_arm_is_recorded_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = world(tmp_path, monkeypatch)

    RUNNER.invoke(research_app, args)
    again = RUNNER.invoke(research_app, args)

    assert again.exit_code == 0, again.output
    assert len((tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    assert "already in the ledger" in again.output


def test_an_unknown_cell_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = world(tmp_path, monkeypatch)

    result = RUNNER.invoke(research_app, [args[0], "a5-etf-dual-momentum", *args[2:]])

    assert result.exit_code == 1
    assert "no core cell" in result.output
