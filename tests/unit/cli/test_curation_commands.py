"""EM-141: `backtest curate --only <name not in the plan>` is refused, not silently empty."""

from __future__ import annotations

from typer.testing import CliRunner

from emporos.cli.main import app

runner = CliRunner()


def invoke(*args: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, list(args))


def test_only_with_a_name_not_in_the_plan_is_refused_before_anything_runs() -> None:
    result = invoke(
        "backtest", "curate", "--from", "2026-01-05", "--to", "2026-01-09",
        "--only", "not_a_real_strategy",
    )  # fmt: skip

    assert result.exit_code == 1 and "curation failed" in result.output
    assert "not_a_real_strategy" in result.output
    assert "orb_v1" in result.output  # the available names are listed


def test_only_with_one_real_and_one_unknown_name_is_still_refused() -> None:
    result = invoke(
        "backtest", "curate", "--from", "2026-01-05", "--to", "2026-01-09",
        "--only", "orb_v1", "--only", "not_a_real_strategy",
    )  # fmt: skip

    assert result.exit_code == 1 and "not_a_real_strategy" in result.output
