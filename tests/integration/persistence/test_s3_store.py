"""S3 store against a real local S3-compatible HTTP endpoint (a moto server) — no AWS
credentials, no Docker. EM-22 acceptance: put/get/list against a local S3 target."""

from __future__ import annotations

import pytest

from emporos.persistence.object_store import ObjectStore, S3ObjectStore
from tests.contract.object_store_contract import ObjectStoreContract

pytestmark = pytest.mark.integration


class TestS3ObjectStoreOverHttp(ObjectStoreContract):
    @pytest.fixture
    def store(self, s3_object_store: S3ObjectStore) -> ObjectStore:
        return s3_object_store
