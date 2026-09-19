"""Typed persistence errors.

A unique-index violation is the database-level guarantee behind order
idempotency (Decision 7) and fill idempotency (§6), so it is surfaced as its
own typed error that callers branch on — never by parsing driver messages.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pymongo.errors import DuplicateKeyError

from emporos.core.errors import DefinitiveError

_INDEX_NAME_PATTERN = re.compile(r"index: (\S+)")


class DuplicateRecordError(DefinitiveError):
    """An insert collided with a unique index. The record already exists — never retry."""

    def __init__(self, collection: str, index_name: str, key_fields: tuple[str, ...]) -> None:
        super().__init__(f"duplicate record in '{collection}' on unique index '{index_name}'")
        self.collection = collection
        self.index_name = index_name
        self.key_fields = key_fields


class SchemaDriftError(DefinitiveError):
    """An existing index disagrees with the declared schema and needs a manual decision."""


class DuplicateKeyTranslator:
    """Turns the driver's `DuplicateKeyError` into a `DuplicateRecordError`."""

    def translate(self, collection: str, error: DuplicateKeyError) -> DuplicateRecordError:
        details: Mapping[str, Any] = error.details or {}
        key_pattern = details.get("keyPattern") or {}
        match = _INDEX_NAME_PATTERN.search(str(details.get("errmsg", error)))
        index_name = match.group(1) if match else "unknown"
        return DuplicateRecordError(collection, index_name, tuple(key_pattern))
