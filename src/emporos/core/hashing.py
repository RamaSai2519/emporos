"""Canonical-JSON content hashing, shared by every layer that versions a document by its content.

`Canonical` is the JSON-safe subset a document must already be in: strings, ints, bools, None,
lists and string-keyed dicts. The hash is SHA-256 over compact, key-sorted JSON, so two documents
with the same content hash identically regardless of key order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

HASH_PREFIX = "sha256:"

Canonical = str | int | bool | None | list["Canonical"] | dict[str, "Canonical"]


def content_hash(document: Mapping[str, Canonical]) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()
