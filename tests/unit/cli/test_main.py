from typer.testing import CliRunner

from emporos.cli.main import app

runner = CliRunner()


def test_help_lists_all_subcommands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("run", "backtest", "backfill", "halt", "db", "instruments"):
        assert command in result.output


def test_placeholder_command_exits_nonzero_with_a_message() -> None:
    result = runner.invoke(app, ["run"])

    assert result.exit_code == 1
    assert "not implemented yet" in result.output


def test_nested_subcommand_group_help() -> None:
    result = runner.invoke(app, ["db", "--help"])

    assert result.exit_code == 0
    assert "migrate" in result.output
