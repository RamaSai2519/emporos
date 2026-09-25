"""EM-233: the screen-rotation command end to end on synthetic ETFs."""

from __future__ import annotations

import json
from collections import defaultdict
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
from emporos.persistence.candle_cold import ParquetCandleCodec
from emporos.research.daily_bars import DailyBarStore
from tests.unit.research.swing.support import bar, sessions

RUNNER = CliRunner()
DAYS = sessions(800, date(2016, 1, 4))
ETFS = {"NIFTYBEES-EQ": "NSE:11", "JUNIORBEES-EQ": "NSE:12", "GOLDBEES-EQ": "NSE:13"}


def path(instrument: str, rate: str) -> list[Candle]:
    price, out = Decimal(100), []
    for d in DAYS:
        nxt = (price * (1 + Decimal(rate))).quantize(Decimal("0.01"))
        out.append(bar(instrument, d, str(price), str(nxt)))
        price = nxt
    return out


def write_cold(root: Path, bars: list[Candle]) -> None:
    months: dict[tuple[str, str], list[Candle]] = defaultdict(list)
    for b in bars:
        months[(b.instrument_id, f"{b.ts:%Y-%m}")].append(b)
    for (instrument, month), part in months.items():
        target = root / "candles" / "1d" / instrument / f"{month}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ParquetCandleCodec().encode(part))


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
        "screen-rotation", "a5-etf-dual-momentum", "--etf-report", str(report),
        "--ledger", str(tmp_path / "screens.jsonl"), "--pnl-dir", str(tmp_path / "pnl"),
        "--root", str(derived), "--bootstrap-paths", "20",
    ]  # fmt: skip


def test_it_screens_the_four_arms_against_the_6040_benchmark_and_reports_the_sub_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = world(tmp_path, monkeypatch)

    result = RUNNER.invoke(research_app, [args[0], *args[1:]])

    assert result.exit_code == 0, result.output
    lines = (tmp_path / "screens.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert {json.loads(line)["max_positions"] for line in lines} == {1, 2}
    assert "60/40 NIFTYBEES/GOLDBEES buy-and-hold rebalanced yearly" in result.output
    assert "cash accrues 5.0% a year" in result.output
    assert "sub-period from 2017-11-13" in result.output
    assert "BENCHMARK 60/40" in result.output
    assert "all-cash months" in result.output
    assert len(list((tmp_path / "pnl").glob("*.csv"))) == 4


def test_a_missing_etf_report_is_a_clean_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = world(tmp_path, monkeypatch)
    (tmp_path / "etf-report.yaml").unlink()

    result = RUNNER.invoke(research_app, [args[0], *args[1:]])

    assert result.exit_code == 1
    assert "screen-rotation failed" in result.output


def test_an_unknown_cell_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = world(tmp_path, monkeypatch)

    result = RUNNER.invoke(research_app, [args[0], "a1-momentum-trend-filter", *args[2:]])

    assert result.exit_code == 1
    assert "no rotation cell" in result.output


def test_the_etf_audit_reports_span_gaps_and_a_unit_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from emporos.cli.etf_bars_commands import _audit
    from emporos.core.config import Settings
    from emporos.research.etf_bars import EtfFetchReport

    monkeypatch.setenv("COLD_ARCHIVE_DIR", str(tmp_path / "cold"))
    monkeypatch.setenv("CANDLE_CACHE_DIR", str(tmp_path / "candles"))  # derived root: beside it
    bars = path("NSE:11", "0.0005")
    split = [
        bar("NSE:11", b.ts.astimezone(tz=None).date(), "10", "10") if i == 300 else b
        for i, b in enumerate(bars)
    ]
    write_cold(tmp_path / "cold", split)
    DailyBarStore(CandleCacheFiles(tmp_path / "candles-derived")).write(
        NIFTY_50, path(NIFTY_50, "0.0005")
    )
    report = EtfFetchReport("NSE:11", "NIFTYBEES-EQ", 800, DAYS[0], DAYS[-1], 3, ())

    document = _audit(Settings.default(), [report], 0, DAYS[0], DAYS[-1])

    (etf,) = document["etfs"]
    assert etf["symbol"] == "NIFTYBEES-EQ"
    assert etf["bars"] == 800
    assert etf["first_day"] == DAYS[0].isoformat()
    assert etf["gap_classes"]  # the one-day 10 print is a large gap: classified, never silent
    assert etf["gaps_against_nifty"] == []  # the NIFTY series was read and every session is there
