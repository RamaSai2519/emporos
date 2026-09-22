from __future__ import annotations

import boto3
from moto import mock_aws

from emporos.cli.cold_storage import cold_archive, cold_object_store
from emporos.core.config import Settings
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.object_store import S3ObjectStore

BUCKET = "emporos-test"


def test_a_deployment_with_an_s3_bucket_points_the_cold_tier_at_s3() -> None:
    with mock_aws():
        boto3.client("s3", region_name="ap-south-1").create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
        )

        settings = Settings(_env_file=None, S3_BUCKET=BUCKET)  # type: ignore[call-arg]
        store = cold_object_store(settings)

        assert isinstance(store, S3ObjectStore)
        assert isinstance(cold_archive(settings), ParquetCandleArchive)