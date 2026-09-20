"""The single `AsyncMongoClient` for the process lifetime (plan.md §6.0, Decision 5).

There is no local Mongo container — every environment, including local dev
and CI, connects directly to the real Atlas cluster via `MONGO_URL`; the only
thing that varies is the resolved database name (`emporos` vs `emporos_dev`,
see `emporos.core.config.Environment`).

Pool size, write concern, etc. are configuration here rather than literals
scattered at call sites, because Decision 5 may later move production to a
single-node on-box deployment where `w="majority"` degrades to a single-node
acknowledgement — that should be a config change, not a code change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
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


class MongoClientFactory:
    """Builds the process Mongo client and resolves the ENV-selected database.

    Composes `Settings` with `ConnectionSettings` so that turning the database
    hosting decision into a config change never touches call sites. One instance
    is created at the composition root and closed during shutdown.
    """

    def __init__(
        self,
        settings: Settings,
        connection_settings: ConnectionSettings = DEFAULT_CONNECTION_SETTINGS,
    ) -> None:
        if not settings.mongo_url:
            raise ConfigurationError("MONGO_URL is not set")
        self._database_name = settings.db_name
        self._client: AsyncMongoClient[Mapping[str, Any]] = AsyncMongoClient(
            settings.mongo_url,
            maxPoolSize=connection_settings.max_pool_size,
            retryWrites=connection_settings.retry_writes,
            w=connection_settings.write_concern,
            serverSelectionTimeoutMS=connection_settings.server_selection_timeout_ms,
            # All timestamps are stored UTC and read back timezone-aware (plan.md §6).
            tz_aware=True,
            tzinfo=UTC,
        )

    @property
    def client(self) -> AsyncMongoClient[Mapping[str, Any]]:
        return self._client

    def database(self) -> AsyncDatabase[Mapping[str, Any]]:
        return self._client[self._database_name]

    async def warm(self) -> None:
        """Open the connection pool with ONE awaited round trip before anything fans out.

        pymongo 4.9.1 can hang when several tasks make their FIRST operations on a cold client at
        once (EM-99 H1); one warm-up call first makes the concurrent ones safe.
        """
        await self._client.admin.command("ping")

    async def close(self) -> None:
        await self._client.close()
