"""EM-117: the trial-ledger commands up to the point they would touch Mongo."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from typer.testing import CliRunner

from emporos.backtest.robustness.program_trials import ProgramTrials
from emporos.backtest.robustness.trials import InMemoryTrialLedger, TrialStatistics
from emporos.cli.main import app
from emporos.cli.trial_commands import _append_missing, _program_summary, _summary
from emporos.domain.experiments import Trial, TrialRole

runner = CliRunner()


def trial(n: int, sharpe: str | None = None, strategy: str = "s") -> Trial:
    return Trial(
        f"t{n}", "exp", strategy, "c", TrialRole.TRAIN, "d", "h", "fees",
        datetime(2026, 9, 20, tzinfo=UTC), daily_sharpe=Decimal(sharpe) if sharpe else None,
    )  # fmt: skip


def test_the_backtest_group_offers_the_ledger() -> None:
    result = runner.invoke(app, ["backtest", "trials", "--help"])

    assert result.exit_code == 0 and "list" in result.output and "backfill" in result.output


def test_curate_can_be_told_not_to_record_and_still_writes_a_json_record_to_a_path() -> None:
    result = runner.invoke(app, ["backtest", "curate", "--help"])

    assert "--no-record" in result.output and "--experiment" in result.output
    assert "--json" in result.output and "--benchmark" in result.output


def test_every_curate_option_name_is_distinct() -> None:
    import typer.main

    command = typer.main.get_command(app).commands["backtest"].commands["curate"]  # type: ignore[attr-defined]
    names = [name for param in command.params for name in param.opts]

    assert len(names) == len(set(names))
    assert "--record" in names and "--json" in names  # the ledger switch and the file path


async def test_backfill_adds_what_is_missing_and_skips_what_is_there() -> None:
    ledger = InMemoryTrialLedger()
    await ledger.append(trial(1))

    first = await _append_missing(ledger, [trial(1), trial(2), trial(3)])
    again = await _append_missing(ledger, [trial(1), trial(2), trial(3)])

    assert first == (2, 1)
    assert again == (0, 3)
    assert len(await ledger.all()) == 3


def test_the_summary_counts_by_experiment_and_strategy() -> None:
    lines = _summary([trial(1, "0.1", "a"), trial(2, "0.3", "a"), trial(3, None, "b")])

    assert lines[0].startswith("3 trials, 2 carry a Sharpe ratio (variance 0.020000")
    assert lines[1:] == ["  exp / a: 2", "  exp / b: 1"]


def test_the_summary_says_when_no_spread_can_be_measured() -> None:
    assert _summary([trial(1), trial(2)])[0] == "2 trials, 0 carry a Sharpe ratio"


def test_the_program_summary_reports_n_and_every_source() -> None:
    program = ProgramTrials(TrialStatistics(42, 5, None), {"strategy trials": 12, "feature": 30})

    assert _program_summary(program) == [
        "program-wide N = 42 (5 carry a Sharpe ratio)",
        "  strategy trials: 12",
        "  feature: 30",
    ]
