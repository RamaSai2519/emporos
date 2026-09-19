"""Wires the concrete Angel One stack. This is composition, so it may name every concrete class
(and only the CLI/worker composition root should call it).

    HttpRestTransport  <-  RateLimitedTransport  <-  RetryingTransport      (= `raw`)
    raw  <-  AngelOneAuthenticator  <-  SessionManager
    raw  <-  AuthenticatedTransport(SessionManager)                          (= `transport`)

Every retry attempt is throttled (retry wraps the limiter), and login/refresh go through `raw`,
so they are throttled and classified but never need a session themselves.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from emporos.broker.angelone.auth import AngelOneAuthenticator
from emporos.broker.angelone.authenticated import AuthenticatedTransport
from emporos.broker.angelone.limits import ANGELONE_RATE_LIMITS
from emporos.broker.angelone.retry import RetryingTransport
from emporos.broker.angelone.session import Credentials, InMemorySessionStore, PyotpTotp
from emporos.broker.angelone.session_manager import SessionManager
from emporos.broker.angelone.throttling import RateLimitedTransport
from emporos.broker.angelone.transport import (
    AngelOneHttpClientFactory,
    ClientIdentity,
    HttpClientFactory,
    HttpRestTransport,
    RestTransport,
)
from emporos.broker.backoff import JitterSource
from emporos.broker.ratelimit import GroupRateLimiter
from emporos.core.clock import Clock, Sleeper
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError


@dataclass(frozen=True)
class AngelOneStack:
    """The wired stack plus the resources it owns."""

    transport: RestTransport
    sessions: SessionManager
    _client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self._client.aclose()


class AngelOneStackFactory:
    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        sleeper: Sleeper,
        jitter: JitterSource,
        http_factory: HttpClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter
        self._http_factory = http_factory or AngelOneHttpClientFactory()

    def build(self) -> AngelOneStack:
        settings = self._require_credentials()
        client = self._http_factory.create()
        raw: RestTransport = RetryingTransport(
            RateLimitedTransport(
                HttpRestTransport(client, settings["api_key"], self._identity()),
                GroupRateLimiter(ANGELONE_RATE_LIMITS, self._clock, self._sleeper),
            ),
            self._sleeper,
            self._jitter,
        )
        authenticator = AngelOneAuthenticator(
            raw,
            Credentials(settings["client_code"], settings["pin"]),
            PyotpTotp(settings["totp_secret"]),
            self._clock,
        )
        sessions = SessionManager(authenticator, InMemorySessionStore(), self._clock)
        return AngelOneStack(AuthenticatedTransport(raw, sessions), sessions, client)

    def _identity(self) -> ClientIdentity:
        defaults = ClientIdentity()
        return ClientIdentity(
            local_ip=self._settings.angelone_local_ip or defaults.local_ip,
            public_ip=self._settings.angelone_public_ip or defaults.public_ip,
            mac_address=self._settings.angelone_mac_address or defaults.mac_address,
        )

    def _require_credentials(self) -> dict[str, str]:
        s = self._settings
        wanted = {
            "ANGELONE_API_KEY": ("api_key", s.angelone_api_key),
            "ANGELONE_CLIENT_CODE": ("client_code", s.angelone_client_code),
            "ANGELONE_PASSWORD": ("pin", s.angelone_password),
            "ANGELONE_TOTP_SECRET": ("totp_secret", s.angelone_totp_secret),
        }
        missing = [name for name, (_, value) in wanted.items() if not value]
        if missing:  # names only — never values
            raise ConfigurationError("missing Angel One settings: " + ", ".join(missing))
        return {key: value for key, value in wanted.values() if value}
