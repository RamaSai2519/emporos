"""ULID generation for idempotency keys and correlation IDs.

ULIDs are used instead of UUID4 because they are lexicographically sortable
by creation time, which makes Mongo range queries and log correlation by
insertion order free. See plan.md Decision 7 for the idempotency-key use case.
"""

from __future__ import annotations

from ulid import ULID


def new_ulid() -> str:
    """A new ULID as its canonical 26-character Crockford base32 string."""
    return str(ULID())


def new_correlation_id() -> str:
    """Alias for `new_ulid`, used where the ID's role is request/log correlation."""
    return new_ulid()


def new_idempotency_key(prefix: str) -> str:
    """A namespaced idempotency key, e.g. `order-01J...`."""
    return f"{prefix}-{new_ulid()}"
