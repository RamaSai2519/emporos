"""The two concrete `LlmClient`s: an OpenAI-compatible chat-completions call, aimed at the Vercel
gateway (Jev, `openai/gpt-4o-mini`) or at OpenAI directly (`gpt-4o-2024-08-06`, pinned) (EM-240).

Own transport as everywhere here: `httpx` with TLS verification on and no switch to turn it off.
Temperature 0, JSON-object mode. A transport failure raises `LlmTransportError` (a stage turns it
into "no trade"); nothing here decides anything."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from emporos.core.clock import AsyncioSleeper, Sleeper
from emporos.core.errors import ConfigurationError, RetryableError
from emporos.eventtrader.llm.client import LlmReply, LlmRequest

__all__ = [
    "GATEWAY_MODEL",
    "GATEWAY_URL",
    "OPENAI_MODEL",
    "OPENAI_URL",
    "LlmTransportError",
    "OpenAiCompatibleClient",
]

GATEWAY_URL = "https://ai-gateway.vercel.sh"
GATEWAY_MODEL = "openai/gpt-4o-mini"
OPENAI_URL = "https://api.openai.com"
OPENAI_MODEL = "gpt-4o-2024-08-06"  # a pinned snapshot: a moving alias could change its cutoff


class LlmTransportError(RetryableError):
    """The call could not be completed (network, HTTP status, an unreadable body)."""


class HttpClientFactory(Protocol):
    def create(self) -> httpx.AsyncClient: ...


@dataclass(frozen=True)
class _Factory:
    base_url: str
    timeout_seconds: float

    def create(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(self.timeout_seconds), verify=True
        )


def _final(status: int) -> bool:
    return status < 500 and status != 429


class OpenAiCompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 60.0,
        attempts: int = 2,
        factory: HttpClientFactory | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        if not api_key:
            raise ConfigurationError("an LLM client needs an API key (from the environment)")
        if attempts < 1:
            raise ValueError("at least one attempt")
        self._key, self._attempts = api_key, attempts
        self._clients = factory or _Factory(base_url, timeout_seconds)
        self._sleeper = sleeper or AsyncioSleeper()

    async def complete(self, request: LlmRequest) -> LlmReply:
        body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": 0,
            "max_tokens": request.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        last = "no attempt"
        for attempt in range(self._attempts):
            try:
                async with self._clients.create() as client:
                    response = await client.post(
                        "/v1/chat/completions", json=body,
                        headers={"Authorization": f"Bearer {self._key}"},
                    )  # fmt: skip
                response.raise_for_status()
                return self._parse(response.json(), request)
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError, KeyError) as error:
                last = f"{type(error).__name__}: {error}"
                if isinstance(error, httpx.HTTPStatusError) and _final(error.response.status_code):
                    break  # a client error will not fix itself
            if attempt < self._attempts - 1:
                await self._sleeper.sleep(2.0 * (attempt + 1))
        raise LlmTransportError(f"{request.stage}: {last}")

    @staticmethod
    def _parse(payload: dict[str, Any], request: LlmRequest) -> LlmReply:
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage") or {}
        if not isinstance(text, str) or not isinstance(usage, dict):
            raise ValueError("unexpected response shape")
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        if tokens_in == 0 and tokens_out == 0:
            # A reply without usage cannot be charged: refuse it rather than count it as free.
            raise ValueError("the response carried no token usage")
        return LlmReply(text, tokens_in, tokens_out, str(payload.get("model", request.model)))
