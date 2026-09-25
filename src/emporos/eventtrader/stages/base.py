"""The shape every LLM stage shares (EM-240).

A stage renders its input to a JSON user message, asks the client once, parses the reply strictly
into a frozen value and, if the reply is not valid, asks ONCE more with a reminder appended (a
different question, so a recorded bad answer is not simply replayed). Two invalid replies, or a
transport failure, end as an outcome with no value and an error: never a guess, never a third try.

A budget refusal or a missing recording is NOT an outcome: it propagates, because a run that
quietly stopped asking would look like a model that declined everything."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Protocol, TypeVar

from emporos.eventtrader.llm.client import LlmClient, LlmReply, LlmRequest
from emporos.eventtrader.llm.http_clients import LlmTransportError
from emporos.eventtrader.llm.reply import InvalidReply, Reply
from emporos.jev.prompts import JevPrompt

__all__ = ["JsonStage", "LlmStage", "StageOutcome"]

_LOG = logging.getLogger(__name__)
_REMINDER = (
    "Your previous reply was not valid. Reply with ONLY the JSON object described in the "
    "instructions, with every field present and in range."
)

In = TypeVar("In", contravariant=True)
Out = TypeVar("Out")
Item = TypeVar("Item")


@dataclass(frozen=True)
class StageOutcome(Generic[Out]):
    value: Out | None
    error: str | None  # "invalid_reply: ..." or "transport: ..."
    attempts: int

    @property
    def ok(self) -> bool:
        return self.value is not None


class LlmStage(Protocol[In, Out]):
    name: str

    async def run(self, item: In, as_of: datetime) -> StageOutcome[Out]: ...


class JsonStage(Generic[Item, Out]):
    """Subclasses say how to render an input and how to read a reply."""

    def __init__(
        self,
        name: str,
        client: LlmClient,
        model: str,
        prompt: JevPrompt,
        max_output_tokens: int = 400,
    ) -> None:
        self.name = name
        self._client, self._model, self._prompt = client, model, prompt
        self._max_output_tokens = max_output_tokens

    def render(self, item: Item) -> Mapping[str, Any]:
        raise NotImplementedError

    def parse(self, reply: Reply) -> Out:
        raise NotImplementedError

    async def run(self, item: Item, as_of: datetime) -> StageOutcome[Out]:
        user = json.dumps(self.render(item), sort_keys=True, separators=(",", ":"))
        error = "no attempt"
        for attempt in (1, 2):
            message = user if attempt == 1 else f"{user}\n\n{_REMINDER}"
            try:
                reply = await self._client.complete(self._request(message, as_of))
            except LlmTransportError as failure:
                return StageOutcome(None, f"transport: {failure}", attempt)
            parsed = self._read(reply)
            if isinstance(parsed, str):
                error = parsed
                _LOG.warning("%s: %s", self.name, parsed)
                continue
            return StageOutcome(parsed, None, attempt)
        return StageOutcome(None, error, 2)

    def _read(self, reply: LlmReply) -> Out | str:
        try:
            return self.parse(Reply(reply.text))
        except InvalidReply as invalid:
            return f"invalid_reply: {invalid}"

    def _request(self, user: str, as_of: datetime) -> LlmRequest:
        return LlmRequest(
            self.name, self._model, self._prompt.version, self._prompt.content_hash,
            self._prompt.system_text, user, self._max_output_tokens, as_of,
        )  # fmt: skip
