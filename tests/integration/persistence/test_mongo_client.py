"""Proves the ENV -> database resolution against the real Atlas cluster
(plan.md §6.0, EM-11/EM-19 acceptance criteria) — never mocked.
"""

import pytest

from emporos.core.config import Settings
from emporos.persistence.mongo import create_mongo_client

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("env", "expected_db"),
    [
        (None, "emporos_dev"),
        ("local", "emporos_dev"),
        ("staging", "emporos_dev"),
        ("main", "emporos"),
    ],
)
async def test_client_connects_to_the_env_resolved_database(
    env: str | None, expected_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if env is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", env)
    settings = Settings()
    if not settings.mongo_url:
        pytest.skip("MONGO_URL not set")
    assert settings.db_name == expected_db

    client = create_mongo_client(settings)
    try:
        db = client[settings.db_name]
        assert db.name == expected_db
        result = await db.command("ping")
        assert result["ok"] == 1.0
    finally:
        await client.close()
