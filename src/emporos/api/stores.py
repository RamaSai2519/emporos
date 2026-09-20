"""The passcode's home: one document in `users`, holding only the Argon2id hash."""

from __future__ import annotations

from emporos.persistence.records import UserRecord
from emporos.persistence.repositories import UserRepository

OPERATOR_ID = "operator"


class MongoPasscodeStore:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def passcode_hash(self) -> str | None:
        record = await self._users.get(OPERATOR_ID)
        if record is None:
            return None
        value = record.model_dump().get("passcode_hash")
        return value if isinstance(value, str) else None

    async def set_passcode_hash(self, hashed: str) -> None:
        await self._users.replace(
            UserRecord.model_validate({"_id": OPERATOR_ID, "passcode_hash": hashed}), upsert=True
        )
