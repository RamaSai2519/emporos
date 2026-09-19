"""The single `AsyncMongoClient` for the process lifetime (plan.md §6.0, Decision 5).

There is no local Mongo container — every environment, including local dev
and CI, connects directly to the real Atlas cluster via `MONGO_URL`; the only
thing that varies is the resolved database name (`emporos` vs `emporos_dev`,
see `emporos.core.config.resolve_db_name`).

Pool size, write concern, etc. are configuration here rather than literals
scattered at call sites, because Decision 5 may later move production to a
single-node on-box deployment where `w="majority"` degrades to a single-node
acknowledgement — that should be a config change, not a code change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError


@dataclass(frozen=True)
class ConnectionSettings:
    max_pool_size: int = 20
    retry_writes: bool = True
    write_concern: str = "majority"
    server_selection_timeout_ms: int = 5000


DEFAULT_CONNECTION_SETTINGS = ConnectionSettings()


def create_mongo_client(
    settings: Settings,
    connection_settings: ConnectionSettings = DEFAULT_CONNECTION_SETTINGS,
) -> AsyncMongoClient[Mapping[str, Any]]:
    """A new client. Callers own its lifecycle — close it when done (e.g. worker
    shutdown, or a test's `finally` block). Process code should get its client
    from a single place wired up at startup, not call this per-request.
    """
    if not settings.mongo_url:
        raise ConfigurationError("MONGO_URL is not set")
    return AsyncMongoClient(
        settings.mongo_url,
        maxPoolSize=connection_settings.max_pool_size,
        retryWrites=connection_settings.retry_writes,
        w=connection_settings.write_concern,
        serverSelectionTimeoutMS=connection_settings.server_selection_timeout_ms,
    )


def get_database(
    client: AsyncMongoClient[Mapping[str, Any]], settings: Settings
) -> AsyncDatabase[Mapping[str, Any]]:
    """The database resolved by `ENV` (plan.md §6.0) — `emporos` iff `ENV=main`,
    `emporos_dev` otherwise."""
    return client[settings.db_name]
