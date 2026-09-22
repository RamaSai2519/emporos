"""`VercelGatewayJevClient` — the one concrete `JevProvider` this package ships (EM-160).

Vercel AI Gateway exposes an OpenAI-compatible `/v1/chat/completions` endpoint in front of many
models; Jev is not a bespoke API, it is a structured-decision prompt sent through that gateway.
The request context goes in as JSON inside the user message; the model is instructed to answer
with nothing but a JSON object, which is parsed strictly — anything that does not parse is a
failure, handled exactly like a timeout (a `JevDecision` with `error` set, never an exception the
caller must remember to catch).

Own transport, as everywhere else in this project: `httpx.AsyncClient` with TLS verification on
and no toggle to turn it off. Retries use the same broker-agnostic exponential-backoff-with-
jitter policy the Angel One adapter uses; concurrency is capped by a semaphore so a burst of
opportunity candidates cannot open unbounded connections to an external service.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from emporos.broker.backoff import BackoffPolicy, JitterSource, RandomJitter
from emporos.core.clock import AsyncioSleeper, Clock, Sleeper, SystemClock
from emporos.jev.config import JevConfig
from emporos.jev.models import ABSTAIN, CONFIRM, REJECT, JevDecision, JevRequest, failed_decision

_LOG = logging.getLogger(__name__)
_VALID_DECISIONS = frozenset({CONFIRM, REJECT, ABSTAIN})

_SYSTEM_PROMPT = (
    "You are a trading decision-support filter. Given structured context about a candidate "
    "trade opportunity, respond with ONLY a JSON object of the form "
    '{"decision": "confirm"|"reject"|"abstain", "confidence": <0..1>, "reason": "<short text>"}. '
    "No other text."
)


class HttpClientFactory(Protocol):
    def create(self) -> httpx.AsyncClient: ...


@dataclass(frozen=True)
class _DefaultHttpClientFactory:
    base_url: str
    timeout_seconds: float

    def create(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_seconds),
            verify=True,  # never optional
        )


class VercelGatewayJevClient:
    def __init__(
        self,
        config: JevConfig,
        api_key: str,
        *,
        clock: Clock | None = None,
        client_factory: HttpClientFactory | None = None,
        backoff: BackoffPolicy | None = None,
        jitter: JitterSource | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self._config = config
        self._api_key = api_key
        self._clock = clock or SystemClock()
        self._clients = client_factory or _DefaultHttpClientFactory(
            config.base_url, config.timeout_seconds
        )
        self._backoff = backoff or BackoffPolicy(base_delay=0.5, max_delay=4.0, max_attempts=1)
        self._jitter = jitter or RandomJitter()
        self._sleeper = sleeper or AsyncioSleeper()
        self._semaphore = asyncio.Semaphore(config.max_concurrency)

    async def decide(self, request: JevRequest) -> JevDecision:
        async with self._semaphore:
            return await self._decide_with_retries(request)

    async def _decide_with_retries(self, request: JevRequest) -> JevDecision:
        started = self._clock.now()
        attempts = max(self._config.max_retries + 1, 1)
        last_error = "unknown error"
        for attempt in range(attempts):
            try:
                return await self._send(request, started)
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as error:
                last_error = f"{type(error).__name__}: {error}"
            except _MalformedResponse as error:
                last_error = str(error)
            if attempt < attempts - 1:
                await self._sleeper.sleep(self._backoff.delay(attempt, self._jitter))
        latency_ms = int((self._clock.now() - started).total_seconds() * 1000)
        _LOG.warning("Jev request failed after %d attempt(s): %s", attempts, last_error)
        return failed_decision(
            "vercel_gateway", error=last_error, requested_at=started, latency_ms=latency_ms
        )

    async def _send(self, request: JevRequest, started: datetime) -> JevDecision:
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(_context(request))},
            ],
            "temperature": 0,
        }
        async with self._clients.create() as client:
            response = await client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        response.raise_for_status()
        return self._parse(response.json(), started)

    def _parse(self, body: dict[str, Any], started: datetime) -> JevDecision:
        latency_ms = int((self._clock.now() - started).total_seconds() * 1000)
        try:
            content = body["choices"][0]["message"]["content"]
            decoded = json.loads(content)
            decision = str(decoded["decision"])
            confidence_raw = decoded.get("confidence")
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise _MalformedResponse(f"could not parse Jev response: {error}") from error
        if decision not in _VALID_DECISIONS:
            decision = ABSTAIN  # an unrecognized decision fails safe, not loud
        confidence = None
        if confidence_raw is not None:
            try:
                confidence = Decimal(str(confidence_raw))
            except InvalidOperation:
                confidence = None
        tokens_used = self._token_count(body)
        return JevDecision(
            decision=decision,
            confidence=confidence,
            provider="vercel_gateway",
            model=self._config.model,
            config_version=None,
            requested_at=started,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
        )

    @staticmethod
    def _token_count(body: dict[str, Any]) -> int | None:
        """The OpenAI-compatible `usage.total_tokens` field, when the gateway sends one — the
        cost proxy EM-160 asks for, since the gateway bills per token, not per request."""
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return None
        total = usage.get("total_tokens")
        return total if isinstance(total, int) else None


class _MalformedResponse(ValueError):
    """Jev answered, but not with parseable JSON in the expected shape."""


def _context(request: JevRequest) -> dict[str, Any]:
    return {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "regime": request.regime,
        "strategy": request.strategy_name,
        "direction": request.direction,
        "entry": str(request.entry),
        "stop": str(request.stop),
        "target": str(request.target),
        "expected_edge": str(request.expected_edge),
        "confidence": str(request.confidence),
        "features": {k: str(v) for k, v in request.features.items()},
        "historical_conditional_performance": {
            k: str(v) for k, v in request.historical_conditional_performance.items()
        },
        "portfolio_context": {k: str(v) for k, v in request.portfolio_context.items()},
    }
