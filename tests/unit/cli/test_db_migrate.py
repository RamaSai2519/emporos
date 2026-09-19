import pytest
from typer.testing import CliRunner

from emporos.cli.main import app
from emporos.core.config import Settings


def test_db_migrate_without_mongo_url_fails_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.setattr(Settings, "default", classmethod(lambda cls: Settings(_env_file=None)))

    result = CliRunner().invoke(app, ["db", "migrate"])

    assert result.exit_code == 1
    assert "MONGO_URL is not set" in result.output
