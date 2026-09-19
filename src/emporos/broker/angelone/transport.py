"""Our own `httpx` transport to SmartAPI REST (Decision 4).

TLS certificate verification is always on: `AngelOneHttpClientFactory` has no knob to turn it
off, which is exactly what the SDK gets wrong. The transport is one layer of a stack — rate
limiting (EM-44) and retry/backoff (EM-45) wrap it as decorators over the `RestTransport`
Protocol rather than being edited into it.

Secrets: the API key and bearer token are only ever placed in request headers. They are never
logged, never put in an exception message, and never appear in a `repr`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from emporos.broker.angelone.classification import ErrorClassifier
from emporos.broker.angelone.endpoints import BASE_URL, Endpoint
from emporos.broker.angelone.envelope import EnvelopeDecoder
from emporos.broker.errors import BrokerError
from emporos.core.errors import ConfigurationError

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientIdentity:
    """The `X-ClientLocalIP` / `X-ClientPublicIP` / `X-MACAddress` headers SmartAPI requires.

    Only order APIs are gated on the *registered* static IP (plan.md §1.3), so a placeholder is
    fine for login, quotes and history; real orders (Phase 7+) need the worker's registered IP."""

    local_ip: str = "127.0.0.1"
    public_ip: str = "127.0.0.1"
    mac_address: str = "00:00:00:00:00:00"


@dataclass(frozen=True)
class RestRequest:
    endpoint: Endpoint
    body: Mapping[str, Any] | None = None
    bearer: str | None = field(default=None, repr=False)


class RestTransport(Protocol):
    """Sends one request and returns the envelope's `data`, or raises a classified `BrokerError`."""

    async def send(self, request: RestRequest) -> Any: ...


class HttpClientFactory(Protocol):
    def create(self) -> httpx.AsyncClient: ...


class AngelOneHttpClientFactory:
    """Builds the `httpx.AsyncClient`. Verification is not a parameter — it cannot be disabled."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
        write_timeout: float = 10.0,
        pool_timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url
        self._timeout = httpx.Timeout(
            connect=connect_timeout, read=read_timeout, write=write_timeout, pool=pool_timeout
        )

    def create(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            verify=True,
            timeout=self._timeout,
            follow_redirects=False,
        )


class HttpRestTransport:
    """The bottom of the stack: one HTTP round trip, decoded and classified."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        identity: ClientIdentity | None = None,
        classifier: ErrorClassifier | None = None,
        decoder: EnvelopeDecoder | None = None,
    ) -> None:
        if not api_key:
            raise ConfigurationError("an Angel One API key is required")
        self._client = client
        self._api_key = api_key
        self._identity = identity or ClientIdentity()
        self._classifier = classifier or ErrorClassifier()
        self._decoder = decoder or EnvelopeDecoder()

    async def send(self, request: RestRequest) -> Any:
        endpoint = request.endpoint
        if endpoint.authenticated and not request.bearer:
            raise ConfigurationError(f"{endpoint.name} needs a session token")
        try:
            response = await self._client.request(
                endpoint.method.value,
                endpoint.path,
                json=request.body,
                headers=self._headers(request),
            )
        except httpx.HTTPError as error:
            failure = self._classifier.for_transport_failure(error, endpoint)
            self._log_failure(endpoint, failure)
            raise failure from error

        envelope = self._decoder.decode(response.content)
        failure_or_none = self._classifier.for_response(response.status_code, envelope, endpoint)
        if failure_or_none is not None:
            self._log_failure(endpoint, failure_or_none)
            raise failure_or_none
        assert envelope is not None  # for_response returns an error whenever there is no envelope
        return envelope.data

    @staticmethod
    def _log_failure(endpoint: Endpoint, failure: BrokerError) -> None:
        _LOG.warning(
            "%s failed: %s [%s]",
            endpoint.name,
            type(failure).__name__,
            failure.classification.value,
        )

    def _headers(self, request: RestRequest) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": self._identity.local_ip,
            "X-ClientPublicIP": self._identity.public_ip,
            "X-MACAddress": self._identity.mac_address,
            "X-PrivateKey": self._api_key,
        }
        if request.bearer:
            headers["Authorization"] = f"Bearer {request.bearer}"
        return headers
