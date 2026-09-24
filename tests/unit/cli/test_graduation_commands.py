"""The graduation CLI: registered, and the acknowledgement refuses to run without a terminal."""

from __future__ import annotations

from typer.testing import CliRunner

from emporos.cli.main import app

runner = CliRunner()


def test_the_group_lists_every_command() -> None:
    result = runner.invoke(app, ["graduation", "--help"])

    assert result.exit_code == 0
    for command in ("status", "promote", "demote", "history", "acknowledge"):
        assert command in result.output


def test_acknowledge_refuses_without_an_interactive_terminal() -> None:
    """CliRunner is not a TTY: a script or a pipe cannot supply the phrase for the operator."""
    result = runner.invoke(app, ["graduation", "acknowledge", "orb_v1"], input="orb_v1@x LIVE\n")

    assert result.exit_code == 1
    assert "interactive terminal" in result.output


def test_a_stage_that_does_not_exist_is_a_usage_error() -> None:
    result = runner.invoke(app, ["graduation", "promote", "orb_v1", "--to", "moon"])

    assert result.exit_code != 0
