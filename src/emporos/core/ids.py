"""ULID generation for idempotency keys and correlation IDs.

ULIDs are used instead of UUID4 because they are lexicographically sortable
by creation time, which makes Mongo range queries and log correlation by
insertion order free. See plan.md Decision 7 for the idempotency-key use case.
"""

from __future__ import annotations

from collections.abc import Callable

from ulid import ULID


class IdGenerator:
    """Mints time-ordered ULID identifiers: correlation IDs and idempotency keys.

    The ULID factory is injected so the source of time/randomness can be pinned
    in tests and backtests without touching the money path.
    """

    def __init__(self, ulid_factory: Callable[[], ULID] = ULID) -> None:
        self._ulid_factory = ulid_factory

    def new_ulid(self) -> str:
        """A new ULID as its canonical 26-character Crockford base32 string."""
        return str(self._ulid_factory())

    def new_correlation_id(self) -> str:
        """A fresh ID for request/log correlation."""
        return self.new_ulid()

    def new_idempotency_key(self, prefix: str) -> str:
        """A namespaced idempotency key, e.g. `order-01J...`."""
        return f"{prefix}-{self.new_ulid()}"
