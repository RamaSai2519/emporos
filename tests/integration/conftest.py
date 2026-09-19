"""Shared fixtures for integration tests against the real Atlas `emporos_dev` database.

`emporos_dev` is a shared database (plan.md §6.0): fixtures must use randomly
suffixed IDs and clean up after themselves. `ENV` is forced to a non-production
value so a stray `ENV=main` in the shell or `.env` can never point a test at
the production database.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import pytest
from moto.server import ThreadedMotoServer
from pymongo.asynchronous.database import AsyncDatabase

from emporos.broker.angelone.factory import AngelOneStack, AngelOneStackFactory
from emporos.broker.backoff import RandomJitter
from emporos.broker.errors import BrokerError
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.object_store import S3ClientFactory, S3ObjectStore

S3_TEST_BUCKET = "emporos-it"


@pytest.fixture
def dev_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("ENV", "local")
    settings = Settings()
    if not settings.mongo_url:
        pytest.skip("MONGO_URL not set")
    assert settings.db_name == "emporos_dev"
    return settings


ANGELONE_SETTINGS = (
    "angelone_api_key",
    "angelone_client_code",
    "angelone_password",
    "angelone_totp_secret",
)


@pytest.fixture
def angelone_settings() -> Settings:
    """Real Angel One credentials from `.env`; the live checks skip cleanly when they are absent."""
    settings = Settings()
    if not all(getattr(settings, name) for name in ANGELONE_SETTINGS):
        pytest.skip("ANGELONE_* credentials not set")
    return settings


LOGIN_COOLDOWN_SECONDS = 1.5  # Angel One allows one login per second across the whole account


@pytest.fixture
async def angelone_stack(angelone_settings: Settings) -> AsyncIterator[AngelOneStack]:
    """A live, wired Angel One stack. Each test builds its own rate limiter, so the teardown
    pauses to keep consecutive tests' logins outside the API's 1-per-second login window."""
    stack = AngelOneStackFactory(
        angelone_settings, SystemClock(), AsyncioSleeper(), RandomJitter()
    ).build()
    try:
        yield stack
    finally:
        with contextlib.suppress(BrokerError):
            await stack.sessions.logout()
        await stack.aclose()
        await asyncio.sleep(LOGIN_COOLDOWN_SECONDS)


@pytest.fixture
async def database(dev_settings: Settings) -> AsyncIterator[AsyncDatabase[Mapping[str, Any]]]:
    factory = MongoClientFactory(dev_settings)
    try:
        yield factory.database()
    finally:
        await factory.close()


@pytest.fixture(scope="module")
def s3_endpoint() -> Iterator[str]:
    """A local S3-compatible HTTP server (moto) — no AWS credentials, no Docker."""
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    try:
        client = S3ClientFactory(Settings(_env_file=None, S3_ENDPOINT_URL=endpoint)).create()  # type: ignore[call-arg]
        # moto's backend state is process-global, so a previous module's server may have made it.
        with contextlib.suppress(client.exceptions.BucketAlreadyOwnedByYou):
            client.create_bucket(
                Bucket=S3_TEST_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
            )
        yield endpoint
    finally:
        server.stop()


@pytest.fixture
def s3_object_store(s3_endpoint: str) -> S3ObjectStore:
    """An empty bucket on the local S3 server, wrapped in the production adapter."""
    settings = Settings(_env_file=None, S3_ENDPOINT_URL=s3_endpoint, S3_BUCKET=S3_TEST_BUCKET)  # type: ignore[call-arg]
    client = S3ClientFactory(settings).create()
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=S3_TEST_BUCKET):
        for item in page.get("Contents", []):
            client.delete_object(Bucket=S3_TEST_BUCKET, Key=item["Key"])
    return S3ObjectStore(client, S3_TEST_BUCKET)
