"""Shared fixtures for integration tests against the real Atlas `emporos_dev` database.

`emporos_dev` is a shared database (plan.md §6.0): fixtures must use randomly
suffixed IDs and clean up after themselves. `ENV` is forced to a non-production
value so a stray `ENV=main` in the shell or `.env` can never point a test at
the production database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.config import Settings
from emporos.persistence.mongo import MongoClientFactory


@pytest.fixture
def dev_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("ENV", "local")
    settings = Settings()
    if not settings.mongo_url:
        pytest.skip("MONGO_URL not set")
    assert settings.db_name == "emporos_dev"
    return settings


@pytest.fixture
async def database(dev_settings: Settings) -> AsyncIterator[AsyncDatabase[Mapping[str, Any]]]:
    factory = MongoClientFactory(dev_settings)
    try:
        yield factory.database()
    finally:
        await factory.close()
