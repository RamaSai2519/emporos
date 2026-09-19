"""S3 store against a real local S3-compatible HTTP endpoint (a moto server) — no AWS
credentials, no Docker. EM-22 acceptance: put/get/list against a local S3 target."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from moto.server import ThreadedMotoServer

from emporos.core.config import Settings
from emporos.persistence.object_store import ObjectStore, S3ClientFactory, S3ObjectStore
from tests.contract.object_store_contract import ObjectStoreContract

pytestmark = pytest.mark.integration

BUCKET = "emporos-it"


@pytest.fixture(scope="module")
def s3_endpoint() -> Iterator[str]:
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    try:
        S3ClientFactory(Settings(_env_file=None, S3_ENDPOINT_URL=endpoint)).create().create_bucket(  # type: ignore[call-arg]
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-south-1"}
        )
        yield endpoint
    finally:
        server.stop()


class TestS3ObjectStoreOverHttp(ObjectStoreContract):
    @pytest.fixture
    def store(self, s3_endpoint: str) -> ObjectStore:
        settings = Settings(_env_file=None, S3_ENDPOINT_URL=s3_endpoint, S3_BUCKET=BUCKET)  # type: ignore[call-arg]
        factory = S3ClientFactory(settings)
        client = factory.create()
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=BUCKET, Key=item["Key"])
        return S3ObjectStore(client, BUCKET)
