"""`emporos instruments sync` against real Atlas: an unreachable upstream must keep the master
and exit 2 so a scheduler can tell it apart from a validation rejection (exit 1).

The full download-and-apply path against the live upstream writes ~23k rows into the shared
`instruments` collection, so it is run deliberately by an operator (see EM-29), not on every
test run; the pipeline itself is covered by the service tests and the master-store tests.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from emporos.cli.main import app

pytestmark = pytest.mark.integration


def test_an_unreachable_upstream_exits_two_and_says_so(
    dev_settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSTRUMENT_MASTER_URL", "http://127.0.0.1:9/OpenAPIScripMaster.json")

    result = CliRunner().invoke(app, ["instruments", "sync"])

    assert result.exit_code == 2, result.output
    assert "instruments sync unavailable" in result.output
