"""Session model, expiry rule and storage seam for Angel One (EM-46).

ONE SESSION PER CLIENT CODE: a new login invalidates the previous one. Logging in from a
developer machine while the production worker is running would kick the worker's session out
mid-day. (Production is not running yet, so local development is fine for now; once it is,
dev must use a separate client code, or the worker must be stopped first.)

SmartAPI force-logs every session out at midnight IST (plan.md §1.2), so re-authenticating every
trading day is routine, and `SessionExpiry` encodes exactly that rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import pyotp

from emporos.core.clock import IST
from emporos.core.errors import ConfigurationError


@dataclass(frozen=True)
class Credentials:
    """Client code + PIN (Angel One calls the PIN "password"). `repr` never shows the PIN."""

    client_code: str
    pin: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.client_code or not self.pin:
            raise ConfigurationError("Angel One client code and PIN are required")


@dataclass(frozen=True)
class Session:
    """One login's tokens. Every token is excluded from `repr` so a stray log line or traceback
    cannot leak it."""

    jwt: str = field(repr=False)
    refresh_token: str = field(repr=False)
    feed_token: str = field(repr=False)
    established_at: datetime
    expires_at: datetime


class SessionExpiry:
    """A session lives until the next midnight IST after it was established."""

    def expires_at(self, established_at: datetime) -> datetime:
        local = established_at.astimezone(IST)
        tomorrow = local + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

    def is_expired(self, session: Session, now: datetime) -> bool:
        return now >= session.expires_at


class SessionStore(Protocol):
    def load(self) -> Session | None: ...

    def save(self, session: Session) -> None: ...

    def clear(self) -> None: ...


class InMemorySessionStore:
    """The worker is the only process that holds a session, and a restart simply logs in again
    (safe: login mutates only the session), so nothing needs to survive the process."""

    def __init__(self) -> None:
        self._session: Session | None = None

    def load(self) -> Session | None:
        return self._session

    def save(self, session: Session) -> None:
        self._session = session

    def clear(self) -> None:
        self._session = None


class TotpSource(Protocol):
    def code(self) -> str: ...


class PyotpTotp:
    """The 6-digit, 30-second rotating code, derived from the enrolled authenticator secret."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ConfigurationError("an Angel One TOTP secret is required")
        self._totp = pyotp.TOTP(secret)

    def code(self) -> str:
        return self._totp.now()
