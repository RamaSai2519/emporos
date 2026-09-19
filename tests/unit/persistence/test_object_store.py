from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.persistence.object_store import (
    ObjectStore,
    ObjectStoreError,
    S3ClientFactory,
    S3ObjectStore,
)
from tests.contract.object_store_contract import ObjectStoreContract
from tests.support.fakes import InMemoryObjectStore

BUCKET = "emporos-test"


class TestInMemoryObjectStore(ObjectStoreContract):
    @pytest.fixture
    def store(self) -> ObjectStore:
        return InMemoryObjectStore()


class TestS3ObjectStoreAgainstMoto(ObjectStoreContract):
    @pytest.fixture
    def store(self) -> Iterator[ObjectStore]:
        with mock_aws():
            client = boto3.client("s3", region_name="ap-south-1")
            client.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
            )
            yield S3ObjectStore(client, BUCKET)


async def test_an_empty_bucket_name_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        S3ObjectStore(boto3.client("s3", region_name="ap-south-1"), "")


async def test_a_missing_bucket_surfaces_as_a_retryable_store_error() -> None:
    with mock_aws():
        store = S3ObjectStore(boto3.client("s3", region_name="ap-south-1"), "no-such-bucket")

        with pytest.raises(ObjectStoreError):
            await store.put("k", b"v")


def test_factory_without_a_bucket_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="S3_BUCKET"):
        S3ClientFactory(Settings(_env_file=None, S3_BUCKET=None)).create_store()  # type: ignore[call-arg]


def test_factory_uses_emulator_credentials_only_with_an_endpoint() -> None:
    local = Settings(_env_file=None, S3_ENDPOINT_URL="http://localhost:9000")  # type: ignore[call-arg]
    aws = Settings(_env_file=None)

    local_client = S3ClientFactory(local).create()

    assert local_client.meta.endpoint_url == "http://localhost:9000"
    assert S3ClientFactory(aws).create().meta.region_name == "ap-south-1"
