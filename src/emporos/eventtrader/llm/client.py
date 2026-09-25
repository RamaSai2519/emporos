"""The one call every stage makes: a system prompt and a JSON user message in, text and token counts
out (EM-240). Everything else (the date guard, the spend ceiling, the record-once journal) is a
decorator over this Protocol, in the order the composition root stacks them."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = ["LlmClient", "LlmReply", "LlmRequest"]


@dataclass(frozen=True)
class LlmRequest:
    stage: str  # "triage", "bull", ... for tallies and the journal
    model: str
    prompt_version: str
    prompt_hash: str  # sha256 of the system prompt
    system: str
    user: str  # a JSON document
    max_output_tokens: int
    # The moment the decision is being made. NEVER sent to the model: for the date guard and the
    # journal only.
    as_of: datetime

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if self.max_output_tokens < 1:
            raise ValueError("a call needs room for an answer")

    @property
    def request_hash(self) -> str:
        """The identity of the question actually asked: model, system prompt and user message."""
        body = "\x1f".join((self.model, self.system, self.user, str(self.max_output_tokens)))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LlmReply:
    text: str
    tokens_in: int
    tokens_out: int
    model: str  # the model that answered (the pinned snapshot for gpt-4o)
    from_journal: bool = False  # answered from a recording: no network, no cost

    def __post_init__(self) -> None:
        if self.tokens_in < 0 or self.tokens_out < 0:
            raise ValueError("token counts cannot be negative")


class LlmClient(Protocol):
    async def complete(self, request: LlmRequest) -> LlmReply: ...
