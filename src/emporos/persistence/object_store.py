"""The object-store abstraction for cold storage (candle Parquet archive, backups, artifacts).

Consumers depend on the small `ObjectStore` Protocol; `S3ObjectStore` is the
production adapter and works unchanged against real S3 or any S3-compatible
target (MinIO, moto) via an endpoint URL, so local development needs no AWS
credentials. boto3 is synchronous, so each call is moved off the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError, DefinitiveError, RetryableError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

T = TypeVar("T")

_NOT_FOUND_CODES = frozenset({"NoSuchKey", "404", "NotFound"})
# Documented dev-only defaults for S3-compatible emulators; never applied to real AWS.
_EMULATOR_CREDENTIALS = ("emporos-local", "emporos-local-secret")


class ObjectNotFoundError(DefinitiveError):
    def __init__(self, key: str) -> None:
        super().__init__(f"object '{key}' does not exist")
        self.key = key


class ObjectStoreError(RetryableError):
    """The store could not be reached or refused the call. Object writes are idempotent."""


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    etag: str


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def stat(self, key: str) -> ObjectInfo | None: ...

    async def list_objects(self, prefix: str) -> list[ObjectInfo]: ...

    async def delete(self, key: str) -> None: ...


class S3ObjectStore:
    def __init__(self, client: S3Client, bucket: str) -> None:
        if not bucket:
            raise ConfigurationError("S3 bucket name is empty")
        self._client = client
        self._bucket = bucket

    async def put(self, key: str, data: bytes) -> None:
        await self._call(
            key, lambda: self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        )

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

        return await self._call(key, read)

    async def stat(self, key: str) -> ObjectInfo | None:
        try:
            head = await self._call(
                key, lambda: self._client.head_object(Bucket=self._bucket, Key=key)
            )
        except ObjectNotFoundError:
            return None
        return ObjectInfo(key, head["ContentLength"], head["ETag"].strip('"'))

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        def collect() -> list[ObjectInfo]:
            pages = self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=prefix
            )
            return [
                ObjectInfo(item["Key"], item["Size"], item["ETag"].strip('"'))
                for page in pages
                for item in page.get("Contents", [])
            ]

        return await self._call(prefix, collect)

    async def delete(self, key: str) -> None:
        await self._call(key, lambda: self._client.delete_object(Bucket=self._bucket, Key=key))

    async def _call(self, key: str, operation: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(operation)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise ObjectNotFoundError(key) from error
            raise ObjectStoreError(f"object store call failed for '{key}': {error}") from error
        except BotoCoreError as error:
            raise ObjectStoreError(f"object store unreachable: {error}") from error


class S3ClientFactory:
    """Builds the boto3 client from `Settings` — the composition-root side of the store."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create(self) -> S3Client:
        settings = self._settings
        if settings.s3_endpoint_url:
            key_id, secret = _EMULATOR_CREDENTIALS
            return boto3.client(
                "s3",
                region_name=settings.aws_region,
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key_id or key_id,
                aws_secret_access_key=settings.s3_secret_access_key or secret,
            )
        return boto3.client("s3", region_name=settings.aws_region)

    def create_store(self) -> S3ObjectStore:
        if not self._settings.s3_bucket:
            raise ConfigurationError("S3_BUCKET is not set")
        return S3ObjectStore(self.create(), self._settings.s3_bucket)
