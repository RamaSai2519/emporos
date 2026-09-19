"""Keeps exactly one valid session alive (EM-46).

* `session()` returns the cached session, or logs in again when there is none or midnight IST
  has passed — the routine daily re-authentication.
* `renew()` handles a session the server rejected mid-day: it tries the cheap token refresh first
  and only falls back to a full TOTP login when the refresh token is itself dead.
* A lock makes concurrent callers share one login/refresh instead of stampeding the 1/s login
  limit or invalidating each other's fresh session.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from emporos.broker.angelone.auth import AngelOneAuthenticator
from emporos.broker.angelone.session import Session, SessionExpiry, SessionStore
from emporos.broker.errors import BrokerAuthError, BrokerSessionExpiredError
from emporos.core.clock import Clock

_LOG = logging.getLogger(__name__)


class SessionProvider(Protocol):
    """What the authenticated transport needs: a valid session, and a way to replace a bad one."""

    async def session(self) -> Session: ...

    async def renew(self, rejected: Session) -> Session: ...


class SessionManager:
    def __init__(
        self,
        authenticator: AngelOneAuthenticator,
        store: SessionStore,
        clock: Clock,
        expiry: SessionExpiry | None = None,
    ) -> None:
        self._authenticator = authenticator
        self._store = store
        self._clock = clock
        self._expiry = expiry or SessionExpiry()
        self._lock = asyncio.Lock()

    async def session(self) -> Session:
        async with self._lock:
            current = self._store.load()
            if current is not None and not self._expiry.is_expired(current, self._clock.now()):
                return current
            if current is not None:
                _LOG.info("session expired at midnight; logging in again")
            return await self._login()

    async def renew(self, rejected: Session) -> Session:
        async with self._lock:
            current = self._store.load()
            if (
                current is not None
                and current.jwt != rejected.jwt
                and not self._expiry.is_expired(current, self._clock.now())
            ):
                return current  # a concurrent caller already replaced the rejected session
            try:
                renewed = await self._authenticator.renew(rejected)
            except (BrokerSessionExpiredError, BrokerAuthError):
                _LOG.info("token refresh refused; falling back to a full login")
                return await self._login()
            self._store.save(renewed)
            return renewed

    async def feed_token(self) -> str:
        return (await self.session()).feed_token

    async def refresh_feed_token(self) -> str:
        """A fresh feed token via token refresh — a full re-login only if the refresh is refused."""
        renewed = await self.renew(await self.session())
        return renewed.feed_token

    async def logout(self) -> None:
        async with self._lock:
            current = self._store.load()
            if current is None:
                return
            try:
                await self._authenticator.logout(current)
            finally:
                self._store.clear()

    async def _login(self) -> Session:
        fresh = await self._authenticator.login()
        self._store.save(fresh)
        return fresh
