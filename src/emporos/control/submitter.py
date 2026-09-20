"""Recording a command. The ONLY thing the API does with an operator's request.

The API process holds no broker credentials and imports no broker code: a command is a validated
document in `commands`, and the worker decides what to do with it. The client-generated idempotency
key, unique in the database, makes a double click, a refresh or a network retry harmless: the second
submit returns the first command instead of creating another. Reusing a key for a DIFFERENT request
is refused — that is a client bug, and silently returning the old command would hide it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Protocol

from emporos.control.commands import CommandStatus, parse_command
from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.persistence.errors import DuplicateRecordError
from emporos.persistence.records import CommandRecord

DEFAULT_TTL = timedelta(minutes=10)


class IdempotencyConflictError(ValueError):
    """The idempotency key was already used for a different command."""


class CommandStore(Protocol):
    async def insert(self, record: CommandRecord) -> None: ...

    async def get_by_idempotency_key(self, idempotency_key: str) -> CommandRecord | None: ...


class CommandSubmitter:
    def __init__(
        self, store: CommandStore, clock: Clock, ids: IdGenerator, ttl: timedelta = DEFAULT_TTL
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("a command must be allowed to wait a positive time")
        self._store = store
        self._clock = clock
        self._ids = ids
        self._ttl = ttl

    async def submit(
        self,
        idempotency_key: str,
        command_type: str,
        params: Mapping[str, Any],
        issued_by: str = "operator",
    ) -> CommandRecord:
        if not idempotency_key.strip():
            raise ValueError("a command needs an idempotency key")
        kind, parsed = parse_command(command_type, params)
        document = parsed.model_dump(mode="json")
        now = self._clock.now()
        record = CommandRecord(
            _id=self._ids.new_ulid(),
            idempotency_key=idempotency_key,
            type=kind.value,
            status=CommandStatus.PENDING.value,
            created_at=now,
            params=document,
            issued_by=issued_by,
            expires_at=now + self._ttl,
            updated_at=now,
        )
        try:
            await self._store.insert(record)
        except DuplicateRecordError:
            existing = await self._store.get_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            if (existing.type, existing.params) != (record.type, record.params):
                raise IdempotencyConflictError(
                    f"idempotency key {idempotency_key!r} was used for a different command"
                ) from None
            return existing
        return record
