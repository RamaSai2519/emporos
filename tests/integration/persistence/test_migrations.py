"""EM-20 acceptance against real Atlas: migrate is idempotent, every §6 index exists,
and a duplicate insert against any unique index raises the expected typed error."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError
from typer.testing import CliRunner

from emporos.cli.main import app
from emporos.core.ids import IdGenerator
from emporos.persistence.collections import Collection
from emporos.persistence.errors import DuplicateKeyTranslator, DuplicateRecordError
from emporos.persistence.migrations import MigrationRunner, MongoSchemaStore
from emporos.persistence.schema import PLATFORM_SCHEMA, IndexSpec

pytestmark = pytest.mark.integration

_UNIQUE_INDEXES = [
    (spec.name, index)
    for spec in PLATFORM_SCHEMA.collections
    for index in spec.indexes
    if index.unique
]


async def test_migrate_creates_every_declared_index_and_is_idempotent(
    database: AsyncDatabase[Mapping[str, Any]],
) -> None:
    runner = MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA)

    await runner.apply()
    second = await runner.apply()

    assert await runner.missing_indexes() == ()
    assert not second.changed


async def test_ticks_is_a_time_series_collection_with_a_seven_day_ttl(
    database: AsyncDatabase[Mapping[str, Any]],
) -> None:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()

    (info,) = [c async for c in await database.list_collections(filter={"name": "ticks"})]

    assert info["type"] == "timeseries"
    assert info["options"]["expireAfterSeconds"] == 7 * 24 * 3600


@pytest.mark.parametrize(
    ("collection", "index"),
    _UNIQUE_INDEXES,
    ids=[f"{collection}.{index.name}" for collection, index in _UNIQUE_INDEXES],
)
async def test_duplicate_insert_on_a_unique_index_raises_the_typed_error(
    database: AsyncDatabase[Mapping[str, Any]], collection: Collection, index: IndexSpec
) -> None:
    await MigrationRunner(MongoSchemaStore(database), PLATFORM_SCHEMA).apply()
    run = IdGenerator().new_ulid()
    unique_fields = {
        name
        for candidate in PLATFORM_SCHEMA.spec_for(collection).indexes
        if candidate.unique
        for name, _ in candidate.keys
    }
    target = {name for name, _ in index.keys}
    first: dict[str, Any] = {field: f"{run}-{field}-first" for field in unique_fields}
    second = {
        field: first[field] if field in target else f"{run}-{field}-second"
        for field in unique_fields
    }
    docs = database[collection]
    inserted = await docs.insert_one(dict(first))
    try:
        with pytest.raises(DuplicateKeyError) as raised:
            await docs.insert_one(dict(second))
        error = DuplicateKeyTranslator().translate(collection, raised.value)

        assert isinstance(error, DuplicateRecordError)
        assert error.collection == collection
        assert set(error.key_fields) == target
    finally:
        await docs.delete_many({"_id": inserted.inserted_id})


def test_cli_db_migrate_is_safe_to_re_run(dev_settings: Any) -> None:
    runner = CliRunner()

    first = runner.invoke(app, ["db", "migrate"])
    second = runner.invoke(app, ["db", "migrate"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "schema already up to date" in second.output
