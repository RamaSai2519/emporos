"""Record once, replay forever, for the free-text stage calls (EM-240).

The key is the question actually asked (`request_hash` covers the model, the system prompt, the user
message and the output allowance): a changed prompt or model is a different question and is never
answered from an old recording. The journal keeps the RAW reply, valid or not, so a replay parses
exactly what the model said."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from emporos.core.errors import DefinitiveError
from emporos.eventtrader.llm.client import LlmClient, LlmReply, LlmRequest

__all__ = [
    "InMemoryJournal",
    "JournaledClient",
    "JsonlJournal",
    "LlmJournal",
    "NotRecorded",
    "Recording",
]


class NotRecorded(DefinitiveError):
    """Replay mode was asked a question that was never recorded."""


@dataclass(frozen=True)
class Recording:
    request_hash: str
    stage: str
    model: str
    prompt_version: str
    prompt_hash: str
    as_of: str  # ISO, for audit only
    text: str
    tokens_in: int
    tokens_out: int
    answered_by: str

    def reply(self) -> LlmReply:
        return LlmReply(self.text, self.tokens_in, self.tokens_out, self.answered_by, True)


class LlmJournal(Protocol):
    def get(self, request_hash: str) -> Recording | None: ...

    def append(self, recording: Recording) -> bool:
        """True when written; False when the question was already recorded (the first stands)."""
        ...


class InMemoryJournal:
    def __init__(self) -> None:
        self._records: dict[str, Recording] = {}

    def get(self, request_hash: str) -> Recording | None:
        return self._records.get(request_hash)

    def append(self, recording: Recording) -> bool:
        if recording.request_hash in self._records:
            return False
        self._records[recording.request_hash] = recording
        return True

    def __len__(self) -> int:
        return len(self._records)


class JsonlJournal:
    """One JSON object per line, appended and never rewritten. Local file: no database load."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._records: dict[str, Recording] | None = None

    def get(self, request_hash: str) -> Recording | None:
        return self._load().get(request_hash)

    def append(self, recording: Recording) -> bool:
        records = self._load()
        if recording.request_hash in records:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(recording.__dict__, sort_keys=True) + "\n")
        records[recording.request_hash] = recording
        return True

    def _load(self) -> dict[str, Recording]:
        if self._records is None:
            self._records = {}
            if self._path.exists():
                for line in self._path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        record = Recording(**json.loads(line))
                        self._records[record.request_hash] = record
        return self._records

    def __len__(self) -> int:
        return len(self._load())


class JournaledClient:
    """`record=True`: a recorded question is answered from the journal, a new one reaches `inner`
    and is recorded. `record=False` (replay): `inner` is never called; a new question raises
    `NotRecorded`, so an incomplete recording is visible and is never papered over with a live
    call."""

    def __init__(self, inner: LlmClient | None, journal: LlmJournal, *, record: bool) -> None:
        if record and inner is None:
            raise ValueError("record mode needs a client to record from")
        self._inner, self._journal, self._record = inner, journal, record
        self.hits = 0  # answered from the journal
        self.fresh = 0  # sent to the model

    async def complete(self, request: LlmRequest) -> LlmReply:
        recorded = self._journal.get(request.request_hash)
        if recorded is not None:
            self.hits += 1
            return recorded.reply()
        if not self._record or self._inner is None:
            raise NotRecorded(f"{request.stage}: {request.request_hash[:12]} was never recorded")
        reply = await self._inner.complete(request)
        self.fresh += 1
        self._journal.append(
            Recording(
                request.request_hash,
                request.stage,
                request.model,
                request.prompt_version,
                request.prompt_hash,
                request.as_of.isoformat(),
                reply.text,
                reply.tokens_in,
                reply.tokens_out,
                reply.model,
            )  # fmt: skip
        )
        return reply
