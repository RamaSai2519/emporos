"""EM-108: the `emporos backtest` and `history fetch-bars` commands, up to the point they would
touch Mongo or the broker (their argument handling and error reporting)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from emporos.cli.main import app

runner = CliRunner()
STRATEGY = Path(__file__).resolve().parents[3] / "config" / "strategies" / "momentum_v1.yaml"


def invoke(*args: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, list(args))


def test_the_backtest_group_lists_run() -> None:
    result = invoke("backtest", "--help")
    assert result.exit_code == 0 and "run" in result.output


def test_a_missing_strategy_file_is_a_usage_error() -> None:
    result = invoke("backtest", "run", "nope.yaml", "--from", "2026-01-05", "--to", "2026-01-09")
    assert result.exit_code == 2


def test_dates_must_be_iso() -> None:
    result = invoke("backtest", "run", str(STRATEGY), "--from", "05/01/2026", "--to", "2026-01-09")
    assert result.exit_code == 2


def test_a_backwards_window_is_refused_before_anything_is_opened() -> None:
    result = invoke("backtest", "run", str(STRATEGY), "--from", "2026-01-09", "--to", "2026-01-05")

    assert result.exit_code == 1 and "backtest failed" in result.output
    assert "first day cannot be after its last" in result.output


def test_a_bad_cash_figure_is_reported_not_a_traceback() -> None:
    result = invoke(
        "backtest", "run", str(STRATEGY), "--from", "2026-01-05", "--to", "2026-01-09",
        "--cash", "lots",
    )  # fmt: skip
    assert result.exit_code == 1 and "backtest failed" in result.output


def test_unknown_fill_settings_are_refused(tmp_path: Path) -> None:
    fills = tmp_path / "fills.yaml"
    fills.write_text("speed: 9\n", encoding="utf-8")

    result = invoke(
        "backtest", "run", str(STRATEGY), "--from", "2026-01-05", "--to", "2026-01-09",
        "--fills", str(fills),
    )  # fmt: skip

    assert result.exit_code == 1 and "backtest failed" in result.output


def test_a_yaml_float_in_fill_settings_is_refused(tmp_path: Path) -> None:
    fills = tmp_path / "fills.yaml"
    fills.write_text("participation: 0.1\n", encoding="utf-8")  # a float has lost exactness

    result = invoke(
        "backtest", "run", str(STRATEGY), "--from", "2026-01-05", "--to", "2026-01-09",
        "--fills", str(fills),
    )  # fmt: skip

    assert result.exit_code == 1


def test_fetch_bars_documents_its_options() -> None:
    result = invoke("history", "fetch-bars", "--help")

    assert result.exit_code == 0
    for option in ("--symbol", "--timeframe", "--from", "--to"):
        assert option in result.output


def test_fetch_bars_refuses_an_underived_timeframe_before_connecting() -> None:
    result = invoke(
        "history",
        "fetch-bars",
        "-s",
        "SBIN-EQ",
        "-t",
        "1m",
        "--from",
        "2026-09-14",
        "--to",
        "2026-09-18",
    )
    assert result.exit_code == 1 and "fetch-bars failed" in result.output
