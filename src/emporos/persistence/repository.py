"""Generic typed repository over one collection.

Every collection is reached through a `Repository` subclass, so no caller
scatters raw collection queries or touches a `Decimal128` by hand. Duplicate-key
violations surface as the typed `DuplicateRecordError`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Generic, TypeVar

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import BulkWriteError, DuplicateKeyError

from emporos.persistence.errors import DuplicateKeyTranslator
from emporos.persistence.records import Record

R = TypeVar("R", bound=Record)

_DUPLICATE_KEY_CODE = 11000


class Repository(Generic[R]):
    """Insert, read, replace and delete records of one type from one collection."""

    def __init__(
        self,
        database: AsyncDatabase[Mapping[str, Any]],
        collection: str,
        record_type: type[R],
        translator: DuplicateKeyTranslator | None = None,
    ) -> None:
        self._collection: AsyncCollection[Mapping[str, Any]] = database[collection]
        self._name = collection
        self._record_type = record_type
        self._translator = translator or DuplicateKeyTranslator()

    async def insert(self, record: R, *, session: AsyncClientSession | None = None) -> None:
        try:
            await self._collection.insert_one(record.to_document(), session=session)
        except DuplicateKeyError as error:
            raise self._translator.translate(self._name, error) from error

    async def insert_many(
        self, records: Sequence[R], *, session: AsyncClientSession | None = None
    ) -> None:
        if not records:
            return
        try:
            await self._collection.insert_many(
                [record.to_document() for record in records], session=session
            )
        except BulkWriteError as error:
            duplicate = self._first_duplicate(error)
            if duplicate is None:
                raise
            raise self._translator.translate(self._name, duplicate) from error

    async def replace(
        self, record: R, *, upsert: bool = False, session: AsyncClientSession | None = None
    ) -> None:
        try:
            await self._collection.replace_one(
                {"_id": record.id}, record.to_document(), upsert=upsert, session=session
            )
        except DuplicateKeyError as error:
            raise self._translator.translate(self._name, error) from error

    async def get(self, record_id: str) -> R | None:
        return await self.find_one({"_id": record_id})

    async def find_one(
        self, query: Mapping[str, Any], *, session: AsyncClientSession | None = None
    ) -> R | None:
        document = await self._collection.find_one(query, session=session)
        return None if document is None else self._record_type.model_validate(document)

    async def find(
        self,
        query: Mapping[str, Any],
        *,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 0,
        session: AsyncClientSession | None = None,
    ) -> list[R]:
        cursor = self._collection.find(query, sort=sort, limit=limit, session=session)
        return [self._record_type.model_validate(document) async for document in cursor]

    async def count(self, query: Mapping[str, Any] | None = None) -> int:
        return await self._collection.count_documents(query or {})

    async def delete(self, record_id: str, *, session: AsyncClientSession | None = None) -> bool:
        result = await self._collection.delete_one({"_id": record_id}, session=session)
        return result.deleted_count == 1

    async def delete_many(
        self, record_ids: Sequence[str], *, session: AsyncClientSession | None = None
    ) -> None:
        if record_ids:
            await self._collection.delete_many({"_id": {"$in": list(record_ids)}}, session=session)

    @staticmethod
    def _first_duplicate(error: BulkWriteError) -> DuplicateKeyError | None:
        for write_error in error.details.get("writeErrors", []):
            if write_error.get("code") == _DUPLICATE_KEY_CODE:
                return DuplicateKeyError(
                    write_error.get("errmsg", ""), _DUPLICATE_KEY_CODE, write_error
                )
        return None
