"""The paper account's login session: simulated, and holding no credentials."""

from __future__ import annotations

from datetime import timedelta

from emporos.broker.models import BrokerSession
from emporos.core.clock import Clock


class PaperSessionKeeper:
    def __init__(self, clock: Clock, client_code: str, lifetime: timedelta) -> None:
        if not client_code:
            raise ValueError("a paper account needs a client code")
        if lifetime <= timedelta(0):
            raise ValueError("a session must have a positive lifetime")
        self._clock = clock
        self._client_code = client_code
        self._lifetime = lifetime
        self._session: BrokerSession | None = None

    @property
    def client_code(self) -> str:
        return self._client_code

    def login(self) -> BrokerSession:
        now = self._clock.now()
        self._session = BrokerSession(self._client_code, now, now + self._lifetime)
        return self._session

    def current(self) -> BrokerSession:
        """The live session, logging in again only if there is none or it has expired."""
        session = self._session
        if session is None or session.expires_at <= self._clock.now():
            return self.login()
        return session

    def logout(self) -> None:
        self._session = None
